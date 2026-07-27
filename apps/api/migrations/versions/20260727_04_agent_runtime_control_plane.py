"""durable agent runtime control plane

Revision ID: 20260727_04
Revises: a87a487dd714
"""
from alembic import op
import sqlalchemy as sa
revision='20260727_04'
down_revision='a87a487dd714'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('agent_runtime_runs',sa.Column('run_id',sa.String(120),primary_key=True),sa.Column('task_id',sa.String(120),nullable=False),sa.Column('agent_id',sa.String(120),nullable=False),sa.Column('parent_run_id',sa.String(120)),sa.Column('state',sa.String(40),nullable=False),sa.Column('version',sa.Integer,nullable=False),sa.Column('event_sequence_number',sa.Integer,nullable=False),sa.Column('attempt_count',sa.Integer,nullable=False),sa.Column('active_attempt_id',sa.String(120)),sa.Column('latest_checkpoint_id',sa.String(120)),sa.Column('recovery_status',sa.String(30),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('deadline',sa.DateTime(timezone=True)),sa.Column('terminal_at',sa.DateTime(timezone=True)),sa.Column('specification_json',sa.Text,nullable=False),sa.Column('snapshot_json',sa.Text,nullable=False))
 for n,c in [('ix_agent_runtime_runs_task_id','task_id'),('ix_agent_runtime_runs_agent_id','agent_id'),('ix_agent_runtime_runs_parent_run_id','parent_run_id'),('ix_agent_runtime_runs_state','state'),('ix_agent_runtime_runs_created_at','created_at'),('ix_agent_runtime_runs_deadline','deadline')]:op.create_index(n,'agent_runtime_runs',[c])
 op.create_index('ix_agent_runtime_runs_nonterminal','agent_runtime_runs',['state','created_at'])
 op.create_table('agent_runtime_events',sa.Column('event_id',sa.String(120),primary_key=True),sa.Column('run_id',sa.String(120),sa.ForeignKey('agent_runtime_runs.run_id',ondelete='CASCADE'),nullable=False),sa.Column('attempt_id',sa.String(120)),sa.Column('event_type',sa.String(80),nullable=False),sa.Column('schema_version',sa.String(20),nullable=False),sa.Column('sequence_number',sa.Integer,nullable=False),sa.Column('run_version',sa.Integer,nullable=False),sa.Column('timestamp',sa.DateTime(timezone=True),nullable=False),sa.Column('actor_reference',sa.String(160)),sa.Column('command_id',sa.String(120)),sa.Column('correlation_id',sa.String(120)),sa.Column('causation_id',sa.String(120)),sa.Column('payload_json',sa.Text,nullable=False),sa.Column('metadata_json',sa.Text,nullable=False),sa.Column('envelope_json',sa.Text,nullable=False),sa.UniqueConstraint('run_id','sequence_number'),sa.UniqueConstraint('run_id','run_version'))
 op.create_index('ix_agent_runtime_events_run_id','agent_runtime_events',['run_id'])
 op.create_table('agent_runtime_attempts',sa.Column('attempt_id',sa.String(120),primary_key=True),sa.Column('run_id',sa.String(120),sa.ForeignKey('agent_runtime_runs.run_id',ondelete='CASCADE'),nullable=False),sa.Column('attempt_number',sa.Integer,nullable=False),sa.Column('contract_json',sa.Text,nullable=False),sa.UniqueConstraint('run_id','attempt_number'))
 op.create_index('ix_agent_runtime_attempts_run_id','agent_runtime_attempts',['run_id'])
 op.create_table('agent_runtime_checkpoints',sa.Column('checkpoint_id',sa.String(120),primary_key=True),sa.Column('run_id',sa.String(120),sa.ForeignKey('agent_runtime_runs.run_id',ondelete='CASCADE'),nullable=False),sa.Column('attempt_id',sa.String(120),nullable=False),sa.Column('checkpoint_sequence',sa.Integer,nullable=False),sa.Column('contract_json',sa.Text,nullable=False),sa.UniqueConstraint('run_id','checkpoint_sequence'))
 op.create_index('ix_agent_runtime_checkpoints_run_id','agent_runtime_checkpoints',['run_id'])
 op.create_table('agent_runtime_processed_commands',sa.Column('run_id',sa.String(120),sa.ForeignKey('agent_runtime_runs.run_id',ondelete='CASCADE'),primary_key=True),sa.Column('command_id',sa.String(120),primary_key=True),sa.Column('command_hash',sa.String(64),nullable=False),sa.Column('command_type',sa.String(120),nullable=False),sa.Column('result_json',sa.Text,nullable=False),sa.Column('processed_at',sa.DateTime(timezone=True),nullable=False))
def downgrade():
 op.drop_table('agent_runtime_processed_commands');op.drop_index('ix_agent_runtime_checkpoints_run_id',table_name='agent_runtime_checkpoints');op.drop_table('agent_runtime_checkpoints');op.drop_index('ix_agent_runtime_attempts_run_id',table_name='agent_runtime_attempts');op.drop_table('agent_runtime_attempts');op.drop_index('ix_agent_runtime_events_run_id',table_name='agent_runtime_events');op.drop_table('agent_runtime_events');op.drop_index('ix_agent_runtime_runs_nonterminal',table_name='agent_runtime_runs')
 for n in ['ix_agent_runtime_runs_deadline','ix_agent_runtime_runs_created_at','ix_agent_runtime_runs_state','ix_agent_runtime_runs_parent_run_id','ix_agent_runtime_runs_agent_id','ix_agent_runtime_runs_task_id']:op.drop_index(n,table_name='agent_runtime_runs')
 op.drop_table('agent_runtime_runs')
