## 2024-07-24 - Avoid deepcopy on Pydantic models for serialization
**Learning:** Using `deepcopy()` on lists containing hundreds of Pydantic models (like `Task`, `Agent`, `AuditEvent`) adds huge CPU overhead and blocks the main thread in FastAPI websocket loops.
**Action:** Instead of `deepcopy()`, directly call `.model_dump(mode="json")` to serialize objects into plain dictionaries, which effectively acts as a fast deep copy and generates the desired JSON representation in one step.
