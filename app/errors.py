"""Errors that decide HTTP status. A failure here never becomes a PASS."""


class ActionGraphError(Exception):
    pass


class PlanValidationError(ActionGraphError):
    pass


class PlanNotFound(ActionGraphError):
    pass


class PlanExists(ActionGraphError):
    pass


class GraphUnavailable(ActionGraphError):
    pass


class ExecutionRejected(ActionGraphError):
    pass


class ReadbackError(ActionGraphError):
    pass
