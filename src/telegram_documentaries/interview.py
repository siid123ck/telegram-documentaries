"""Interview orchestrator: the conversation engine after a photo passes.

This module decides what happens next in an interview and writes the outcome
through the :class:`SessionStore` driver. It emits no Telegram calls: each entry
point returns a typed :class:`Reply` (text to send and the resulting phase).
Provider failures are raised as the typed errors from :mod:`errors`, and the
routing layer maps them to user-facing copy.

The 5-to-7 answer range is enforced here, in code, whatever the model returns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram_documentaries.errors import InvalidResponse
from telegram_documentaries.interview_client import (
    InterviewClient,
    next_turn_with_retry,
)
from telegram_documentaries.interview_models import (
    MAX_ANSWER_CHARS,
    MAX_ANSWERS,
    MIN_ANSWERS,
    Answer,
    Dossier,
    InterviewTurn,
)
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.messages import (
    ANSWER_TOO_LONG_TEXT,
    EMPTY_ANSWER_TEXT,
    NO_QUESTION_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    render_dossier,
)
from telegram_documentaries.models import ChatId
from telegram_documentaries.prompts import (
    INTERVIEW_FINISH_NOTE,
    INTERVIEW_REPAIR_TEMPLATE,
)
from telegram_documentaries.state import Phase, SessionStore

logger = logging.getLogger(__name__)

#: Longest validation summary echoed back to the model in a repair request.
MAX_REPAIR_DETAIL_CHARS = 500


@dataclass(frozen=True)
class Reply:
    """What to send to the chat, and the phase the chat is now in."""

    text: str
    phase: Phase


def _accept(
    turn: InterviewTurn, history: list[Answer], must_finish: bool
) -> InterviewTurn:
    """Apply the orchestrator's rules on top of the schema rules.

    Runs inside the repair loop, so any failure here gets the same single repair
    as a schema failure. ``finish`` before the minimum answer count is invalid.
    Once the maximum is reached the model must finish, so another ``ask`` is
    invalid too.
    """
    if turn.action == "ask" and turn.question is None:
        raise InvalidResponse(detail="ask turn without a question")
    if turn.action == "finish" and turn.dossier is None:
        raise InvalidResponse(detail="finish turn without a dossier")
    if turn.action == "finish" and len(history) < MIN_ANSWERS:
        raise InvalidResponse(detail="finished before the minimum number of answers")
    if must_finish and turn.action == "ask":
        raise InvalidResponse(detail="asked another question after the final answer")
    return turn


def _ask_text(turn: InterviewTurn) -> str:
    """Return the question of a validated ``ask`` turn (for the type checker)."""
    if turn.question is None:
        raise InvalidResponse(detail="ask turn without a question")
    return turn.question


def _dossier_of(turn: InterviewTurn) -> Dossier:
    """Return the dossier of a validated ``finish`` turn (for the type checker)."""
    if turn.dossier is None:
        raise InvalidResponse(detail="finish turn without a dossier")
    return turn.dossier


def _repair_note(error: InvalidResponse, must_finish: bool) -> str:
    """Build the one-shot repair request for an invalid model reply."""
    detail = error.detail[:MAX_REPAIR_DETAIL_CHARS] or "the reply had the wrong shape"
    note = INTERVIEW_REPAIR_TEMPLATE.format(detail=detail)
    if must_finish:
        note = f"{note} {INTERVIEW_FINISH_NOTE}"
    return note


async def _request_turn(
    client: InterviewClient, history: list[Answer], *, must_finish: bool
) -> InterviewTurn:
    """Ask for the next turn, with exactly one repair attempt on an invalid reply.

    A reply that is invalid on the first attempt is logged at ``error`` even when
    the repair succeeds, because the model broke its contract. A second invalid
    reply propagates as :class:`InvalidResponse`.
    """
    note = INTERVIEW_FINISH_NOTE if must_finish else None
    try:
        turn = await next_turn_with_retry(client, history, note=note)
        return _accept(turn, history, must_finish)
    except InvalidResponse as exc:
        logger.error(
            "interview_response_invalid repair=attempted error_type=InvalidResponse"
        )
        repair = _repair_note(exc, must_finish)
    turn = await next_turn_with_retry(client, history, note=repair)
    return _accept(turn, history, must_finish)


@log_stage
async def start_interview(
    chat_id: ChatId,
    photo_file_id: str,
    client: InterviewClient,
    store: SessionStore,
) -> Reply:
    """Start the interview after a passing photo and ask the first question.

    The photo is recorded only when the first question is committed, so a failed
    start leaves the chat in ``AWAITING_PHOTO`` with no photo stored.
    """
    phase_before = store.get(chat_id).phase
    if not store.begin_turn(chat_id):
        return Reply(ONE_AT_A_TIME_TEXT, phase_before)
    try:
        turn = await _request_turn(client, [], must_finish=False)
        question = _ask_text(turn)
        store.commit_first_question(chat_id, photo_file_id, question)
    finally:
        store.release_turn(chat_id)
    logger.info(
        "interview_started chat_id=%s phase_before=%s phase_after=%s question_index=0",
        chat_id,
        phase_before,
        Phase.INTERVIEWING,
    )
    return Reply(question, Phase.INTERVIEWING)


@log_stage
async def answer_question(
    chat_id: ChatId,
    text: str,
    client: InterviewClient,
    store: SessionStore,
) -> Reply:
    """Handle one text answer to the outstanding question.

    Blank and over-long answers are nudged without a model call. A valid answer
    is committed only once the provider returns a valid turn; a failed turn raises
    and leaves the answer uncommitted, so the user can resend it. Reaching the
    maximum answer count forces the model to finish.
    """
    state = store.get(chat_id)
    question = state.outstanding_question
    if state.phase is not Phase.INTERVIEWING or question is None:
        logger.warning(
            "interview_text_without_question chat_id=%s phase=%s",
            chat_id,
            state.phase,
        )
        return Reply(NO_QUESTION_TEXT, state.phase)

    cleaned = text.strip()
    if not cleaned:
        logger.info("interview_answer_nudged reason=empty chat_id=%s", chat_id)
        return Reply(EMPTY_ANSWER_TEXT, Phase.INTERVIEWING)
    if len(cleaned) > MAX_ANSWER_CHARS:
        logger.info("interview_answer_nudged reason=too_long chat_id=%s", chat_id)
        return Reply(ANSWER_TOO_LONG_TEXT, Phase.INTERVIEWING)

    if not store.begin_turn(chat_id):
        return Reply(ONE_AT_A_TIME_TEXT, Phase.INTERVIEWING)
    try:
        answer = Answer(question=question, answer=cleaned)
        history = [*state.answers, answer]
        must_finish = len(history) >= MAX_ANSWERS
        turn = await _request_turn(client, history, must_finish=must_finish)
        # Narrow the turn before any commit, so a bad shape never leaves a
        # half-committed exchange behind.
        if turn.action == "ask":
            next_question = _ask_text(turn)
            store.commit_answer(chat_id, answer)
            store.commit_question(chat_id, next_question)
            logger.info(
                "interview_turn chat_id=%s phase_before=%s phase_after=%s "
                "outcome=ask question_index=%d",
                chat_id,
                Phase.INTERVIEWING,
                Phase.INTERVIEWING,
                len(history),
            )
            return Reply(next_question, Phase.INTERVIEWING)

        dossier = _dossier_of(turn)
        store.commit_answer(chat_id, answer)
        store.commit_dossier(chat_id, dossier)
        logger.info(
            "interview_turn chat_id=%s phase_before=%s phase_after=%s "
            "outcome=finish question_index=%d animal=%s",
            chat_id,
            Phase.INTERVIEWING,
            Phase.DOSSIER_READY,
            len(history),
            dossier.suggested_animal.name,
        )
        return Reply(render_dossier(dossier), Phase.DOSSIER_READY)
    finally:
        store.release_turn(chat_id)


@log_stage
def restart(chat_id: ChatId, store: SessionStore) -> Reply:
    """Discard everything for the chat and send the opening prompt."""
    phase_before = store.get(chat_id).phase
    store.reset(chat_id)
    logger.info(
        "interview_restarted chat_id=%s phase_before=%s phase_after=%s",
        chat_id,
        phase_before,
        Phase.AWAITING_PHOTO,
    )
    return Reply(OPENING_PROMPT, Phase.AWAITING_PHOTO)
