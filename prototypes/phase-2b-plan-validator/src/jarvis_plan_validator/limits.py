from dataclasses import dataclass

@dataclass
class ConfigurationLimits:
    max_raw_response_bytes: int = 100_000
    max_json_depth: int = 20
    max_total_fields: int = 1000
    max_total_string_chars: int = 50_000
    max_assumptions: int = 20
    max_completion_criteria: int = 20
    max_steps: int = 50
    max_dependencies_per_step: int = 20
    max_parameters_per_step: int = 50
    max_parameter_string_length: int = 5000
    max_retry_attempts: int = 10
    max_expected_output_length: int = 500

DEFAULT_LIMITS = ConfigurationLimits()
