class ValidatorError(Exception):
    """Base class for all validator exceptions."""
    pass

class SchemaValidationError(ValidatorError):
    """Raised when an input fails JSON schema validation."""
    pass
