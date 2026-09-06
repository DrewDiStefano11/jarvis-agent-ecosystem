"""Durable untrusted source catalog, immutable provenance, explicit activation."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_09"
down_revision = "20260905_08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_sources",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("commit", sa.String(40), nullable=False),
        sa.Column("license", sa.String(100), nullable=False),
        sa.Column("license_text", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("provider", "commit"),
    )
    op.create_table(
        "catalog_entries",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("stable_key", sa.String(160), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("current_revision_id", sa.String(80)),
        sa.Column("duplicate_of", sa.String(80), sa.ForeignKey("catalog_entries.id")),
        sa.Column("duplicate_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('agent','skill','discovery')"),
    )
    for name in ("kind", "duplicate_key"):
        op.create_index(f"ix_catalog_entries_{name}", "catalog_entries", [name])
    op.create_table(
        "catalog_revisions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("entry_id", sa.String(80), sa.ForeignKey("catalog_entries.id"), nullable=False),
        sa.Column("source_id", sa.String(80), sa.ForeignKey("catalog_sources.id"), nullable=False),
        sa.Column("source_path", sa.String(500), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("normalized", sa.JSON(), nullable=False),
        sa.Column("original_definition", sa.Text(), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_id", "source_id", "parser_version"),
        sa.CheckConstraint("review_status IN ('unreviewed','approved','rejected')"),
    )
    op.create_index("ix_catalog_revisions_entry_id", "catalog_revisions", ["entry_id"])
    op.create_table(
        "catalog_activations",
        sa.Column("entry_id", sa.String(80), sa.ForeignKey("catalog_entries.id"), primary_key=True),
        sa.Column(
            "revision_id", sa.String(80), sa.ForeignKey("catalog_revisions.id"), nullable=False
        ),
        sa.Column(
            "identity_id",
            sa.String(80),
            sa.ForeignKey("identity_agents.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # SQLite is the supported local persistence engine. Source payloads are
    # append-only; review status remains independently mutable.
    op.execute("""CREATE TRIGGER catalog_revision_immutable BEFORE UPDATE OF
        entry_id, source_id, source_path, source_hash, parser_version, normalized,
        original_definition, imported_at ON catalog_revisions
        BEGIN SELECT RAISE(ABORT, 'catalog revision is immutable'); END""")
    op.execute("""CREATE TRIGGER catalog_source_immutable BEFORE UPDATE ON catalog_sources
        BEGIN SELECT RAISE(ABORT, 'catalog source is immutable'); END""")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM catalog_entries")).scalar():
        raise RuntimeError("Export catalog provenance before downgrading populated revision 09")
    op.drop_table("catalog_activations")
    op.drop_table("catalog_revisions")
    op.drop_table("catalog_entries")
    op.drop_table("catalog_sources")
