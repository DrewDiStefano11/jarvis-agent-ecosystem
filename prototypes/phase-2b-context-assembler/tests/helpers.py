from jarvis_context_assembler.contracts import ContextSource, ContextSourceMetadata, ContextPolicy, TaskRequest

def create_mock_source(
    source_id="src-001",
    source_type="repository_file",
    trust_level="repository_content",
    project_id="jarvis-agent-ecosystem",
    content="Test content",
    content_hash=None,
    exact=False,
    inclusion_priority=0,
    approved=True
):
    from jarvis_context_assembler.hashing import hash_content
    chash = content_hash or hash_content(content)
    return ContextSource(
        source_id=source_id,
        source_type=source_type,
        trust_level=trust_level,
        title="Test Title",
        content=content,
        content_hash=chash,
        metadata=ContextSourceMetadata(
            project_id=project_id,
            approved=approved,
            exact_preservation_required=exact,
            inclusion_priority=inclusion_priority
        )
    )

def create_mock_policy():
    return ContextPolicy(
        policy_version="test-v1",
        estimated_token_budget=1000,
        maximum_context_tokens=1000,
        reserved_output_tokens=200,
        allowed_source_types=["repository_file"],
        allowed_trust_levels=["repository_content", "system_policy"],
        cross_project_context_allowed=False
    )

def create_mock_task():
    return TaskRequest(
        task_id="task-001",
        project_id="jarvis-agent-ecosystem",
        original_request="Test task",
        allowed_result_type="json",
        completion_criteria="Done"
    )
