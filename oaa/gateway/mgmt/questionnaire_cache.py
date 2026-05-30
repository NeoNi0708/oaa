"""In-memory cache for multi-page questionnaire partial submissions.

Key: qnr_id -> {sec_id: {q_id: value}}
Created lazily on first submit_section for a given qnr_id.
"""


class QuestionnaireCache:
    """In-memory cache for questionnaire partial submissions."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def save_section(self, qnr_id: str, section_id: str, answers: dict) -> None:
        """Cache one page's answers. Auto-creates entry if new."""
        if qnr_id not in self._store:
            self._store[qnr_id] = {}
        self._store[qnr_id][section_id] = answers

    def get_all_answers(self, qnr_id: str) -> dict | None:
        """Return {sec_id: {q_id: value}} for all submitted sections."""
        return self._store.get(qnr_id)

    def drop(self, qnr_id: str) -> None:
        """Remove a completed entry."""
        self._store.pop(qnr_id, None)
