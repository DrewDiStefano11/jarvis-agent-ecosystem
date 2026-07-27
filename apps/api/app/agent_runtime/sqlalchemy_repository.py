"""SQLAlchemy persistence adapter for the authoritative runtime ledger."""
from __future__ import annotations
import json
from datetime import UTC
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from app.agent_runtime.errors import CommandConflictError, LedgerReplayError, LedgerSequenceError, RunAlreadyExistsError, RunNotFoundError, VersionConflictError
from app.agent_runtime.ledger import replay_execution_ledger
from app.agent_runtime.repository import AgentRuntimeRepository, validate_lineage_invariant
from app.agent_runtime.transitions import TERMINAL_STATES
from app.db.models import AgentRuntimeAttemptRow,AgentRuntimeCheckpointRow,AgentRuntimeEventRow,AgentRuntimeProcessedCommandRow,AgentRuntimeRunRow
from app.models.agent_runtime import AgentRunAttempt,AgentRunCheckpoint,AgentRunQuery,AgentRunQueryResult,AgentRunSnapshot,ProcessedCommandRecord,RuntimeCommandResult,RuntimeEventEnvelope,canonical_json

def dump(x): return canonical_json(x.model_dump(mode="json"))
def load(text,cls): return cls.model_validate(json.loads(text))
class SqlAlchemyAgentRuntimeRepository(AgentRuntimeRepository):
 def __init__(self,sessions:sessionmaker[Session]): self.sessions=sessions
 def load_run(self,run_id):
  with self.sessions() as s:
   x=s.get(AgentRuntimeRunRow,run_id);return None if x is None else load(x.snapshot_json,AgentRunSnapshot)
 def load_run_state(self,run_id):
  with self.sessions() as s:
   x=s.get(AgentRuntimeRunRow,run_id)
   if not x:return None
   es=s.scalars(select(AgentRuntimeEventRow).where(AgentRuntimeEventRow.run_id==run_id).order_by(AgentRuntimeEventRow.sequence_number)).all();return load(x.snapshot_json,AgentRunSnapshot),[load(e.envelope_json,RuntimeEventEnvelope) for e in es]
 def list_events(self,run_id):
  x=self.load_run_state(run_id)
  if not x:raise RunNotFoundError(run_id=run_id)
  return x[1]
 def load_attempt_history(self,run_id): return self._contracts(run_id,AgentRuntimeAttemptRow,AgentRunAttempt,"attempt_number")
 def list_checkpoints(self,run_id): return self._contracts(run_id,AgentRuntimeCheckpointRow,AgentRunCheckpoint,"checkpoint_sequence")
 def _contracts(self,run_id,row,cls,order):
  with self.sessions() as s:
   if not s.get(AgentRuntimeRunRow,run_id):raise RunNotFoundError(run_id=run_id)
   return [load(x.contract_json,cls) for x in s.scalars(select(row).where(row.run_id==run_id).order_by(getattr(row,order))).all()]
 def get_processed_command(self,run_id,command_id):
  with self.sessions() as s:
   x=s.get(AgentRuntimeProcessedCommandRow,(run_id,command_id));return None if not x else ProcessedCommandRecord(run_id=run_id,command_id=command_id,command_hash=x.command_hash,result=load(x.result_json,RuntimeCommandResult),recorded_at=x.processed_at.replace(tzinfo=UTC) if x.processed_at.tzinfo is None else x.processed_at)
 def create_run(self,snapshot,*,events=()): raise RuntimeError("Use commit_command")
 def save_run(self,snapshot,*,expected_version):
  old=self.load_run(snapshot.specification.run_id)
  if old is None:raise RunNotFoundError(run_id=snapshot.specification.run_id)
  if old.version!=expected_version or old!=snapshot:raise VersionConflictError(run_id=snapshot.specification.run_id)
 def append_events(self,run_id,events,*,expected_sequence): raise LedgerSequenceError("Use command commits.",run_id=run_id)
 def store_processed_command_result(self,record):
  with self.sessions.begin() as s:self._store(s,record)
 def query_runs(self,q):
  with self.sessions() as s:
   st=select(AgentRuntimeRunRow)
   for c,v in ((AgentRuntimeRunRow.run_id,q.run_id),(AgentRuntimeRunRow.task_id,q.task_id),(AgentRuntimeRunRow.agent_id,q.agent_id),(AgentRuntimeRunRow.parent_run_id,q.parent_run_id),(AgentRuntimeRunRow.state,None if not q.state else q.state.value)):
    if v is not None:st=st.where(c==v)
   if q.terminal is not None:st=st.where(AgentRuntimeRunRow.state.in_([x.value for x in TERMINAL_STATES]) if q.terminal else AgentRuntimeRunRow.state.not_in([x.value for x in TERMINAL_STATES]))
   total=s.scalar(select(func.count()).select_from(st.subquery())) or 0;rows=s.scalars(st.order_by(AgentRuntimeRunRow.created_at,AgentRuntimeRunRow.run_id).offset(q.offset).limit(q.limit)).all();return AgentRunQueryResult(items=tuple(load(x.snapshot_json,AgentRunSnapshot) for x in rows),offset=q.offset,limit=q.limit,next_offset=q.offset+q.limit if q.offset+q.limit<total else None,total_count=total)
 def commit_command(self,*,snapshot,events,processed_command,expected_version,expected_sequence,create=False):
  run_id=snapshot.specification.run_id
  try:
   with self.sessions.begin() as s:
    prior=s.get(AgentRuntimeProcessedCommandRow,(run_id,processed_command.command_id))
    if prior:
     if prior.command_hash!=processed_command.command_hash:raise CommandConflictError(run_id=run_id,command_id=processed_command.command_id)
     return ProcessedCommandRecord(run_id=run_id,command_id=prior.command_id,command_hash=prior.command_hash,result=load(prior.result_json,RuntimeCommandResult),recorded_at=prior.processed_at.replace(tzinfo=UTC) if prior.processed_at.tzinfo is None else prior.processed_at)
    row=s.get(AgentRuntimeRunRow,run_id); old=[]
    if create:
     if row:raise RunAlreadyExistsError(run_id=run_id)
     validate_lineage_invariant(run_id,snapshot.specification.parent_run_id,lookup=lambda i:None if (r:=s.get(AgentRuntimeRunRow,i)) is None else load(r.snapshot_json,AgentRunSnapshot))
    else:
     if not row:raise RunNotFoundError(run_id=run_id)
     if row.version!=expected_version:raise VersionConflictError(run_id=run_id,command_id=processed_command.command_id)
     old=[load(e.envelope_json,RuntimeEventEnvelope) for e in s.scalars(select(AgentRuntimeEventRow).where(AgentRuntimeEventRow.run_id==run_id).order_by(AgentRuntimeEventRow.sequence_number))]
     if len(old)!=expected_sequence:raise LedgerSequenceError(run_id=run_id)
    ag=replay_execution_ledger(old+list(events))
    if not ag or ag.snapshot!=snapshot:raise LedgerReplayError("Ledger/projection mismatch",run_id=run_id)
    if create:s.add(AgentRuntimeRunRow(run_id=run_id,task_id=snapshot.specification.task_id,agent_id=snapshot.specification.agent_id,parent_run_id=snapshot.specification.parent_run_id,state=snapshot.state.value,version=snapshot.version,event_sequence_number=snapshot.event_sequence_number,attempt_count=snapshot.attempt_count,active_attempt_id=snapshot.active_attempt_id,latest_checkpoint_id=snapshot.latest_checkpoint_id,recovery_status=snapshot.recovery_status.value,created_at=snapshot.created_at,updated_at=snapshot.created_at,deadline=snapshot.specification.deadline,terminal_at=snapshot.completed_at,specification_json=dump(snapshot.specification),snapshot_json=dump(snapshot)))
    else:
     row.state=snapshot.state.value;row.version=snapshot.version;row.event_sequence_number=snapshot.event_sequence_number;row.attempt_count=snapshot.attempt_count;row.active_attempt_id=snapshot.active_attempt_id;row.latest_checkpoint_id=snapshot.latest_checkpoint_id;row.recovery_status=snapshot.recovery_status.value;row.terminal_at=snapshot.completed_at;row.snapshot_json=dump(snapshot)
    for e in events:s.add(AgentRuntimeEventRow(event_id=e.event_id,run_id=e.run_id,attempt_id=e.attempt_id,event_type=e.event_type.value,schema_version=e.event_schema_version,sequence_number=e.sequence_number,run_version=e.run_version,timestamp=e.timestamp,actor_reference=e.actor_reference,command_id=e.command_id,correlation_id=e.correlation_id,causation_id=e.causation_id,payload_json=canonical_json(e.payload),metadata_json=canonical_json(e.metadata),envelope_json=dump(e)))
    s.query(AgentRuntimeCheckpointRow).filter_by(run_id=run_id).delete();s.query(AgentRuntimeAttemptRow).filter_by(run_id=run_id).delete()
    for x in ag.attempts:s.add(AgentRuntimeAttemptRow(attempt_id=x.attempt_id,run_id=run_id,attempt_number=x.attempt_number,contract_json=dump(x)))
    for x in ag.checkpoints:s.add(AgentRuntimeCheckpointRow(checkpoint_id=x.checkpoint_id,run_id=run_id,attempt_id=x.attempt_id,checkpoint_sequence=x.checkpoint_sequence,contract_json=dump(x)))
    self._store(s,processed_command);s.flush();return None
  except IntegrityError as e:raise VersionConflictError("Concurrent runtime mutation rejected.",run_id=run_id) from e
 def _store(self,s,x):s.add(AgentRuntimeProcessedCommandRow(run_id=x.run_id,command_id=x.command_id,command_hash=x.command_hash,command_type="runtime",result_json=dump(x.result),processed_at=x.recorded_at))
 def integrity_check(self,run_id):
  state=self.load_run_state(run_id)
  if not state:raise RunNotFoundError(run_id=run_id)
  ag=replay_execution_ledger(state[1])
  if not ag or ag.snapshot!=state[0] or ag.attempts!=self.load_attempt_history(run_id) or ag.checkpoints!=self.list_checkpoints(run_id):raise LedgerReplayError("Durable projection mismatch",run_id=run_id)
  return True
