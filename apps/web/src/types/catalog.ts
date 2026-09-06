export interface CatalogEntry {
  id: string; kind: 'agent' | 'skill' | 'discovery'; stable_key: string
  display_name: string; description: string; role: string; capabilities: string[]
  source_repository: string; source_commit: string; source_path: string; source_license: string
  revision_id: string; review_status: string; trust_status: string; enabled: boolean
  duplicate_of: string | null; identity_id: string | null; active_revision_id: string | null
  update_available: boolean; operational_status: string | null; lifecycle_state: string | null
  runtime_enabled: boolean | null; warnings: string[]
}
export interface CatalogDetail extends CatalogEntry {
  original_definition: string; source_hash: string; parser_version: string; license_text: string
  imported_at: string; revisions: string[]
  normalized: { requested_tool_classes: string[]; unmapped_tags: string[]; references: string[]; applicable_agent_classes: string[] }
}
export interface CatalogPage { items: CatalogEntry[]; total: number; offset: number; limit: number }
export interface CatalogSource {
  id: string; provider: string; repository: string; commit: string; license: string; imported_at: string; imported_count: number
}
