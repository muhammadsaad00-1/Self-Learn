// Friendly names for the generated API types. schema.ts is generated from the backend's
// OpenAPI schema (`make api-types`), so a backend contract change shows up here as a
// TypeScript compile error instead of a runtime bug. Never edit schema.ts by hand.
import type { components } from './schema';

type Schemas = components['schemas'];

export type Role = Schemas['Role'];
export type TokenResponse = Schemas['TokenResponse'];
export type SignupRequest = Schemas['SignupRequest'];
export type SignupResponse = Schemas['SignupResponse'];
export type MeResponse = Schemas['MeResponse'];
export type Membership = Schemas['MembershipOut'];
export type Member = Schemas['MemberOut'];
export type AddMemberRequest = Schemas['AddMemberRequest'];
export type AuditEvent = Schemas['AuditEventOut'];
export type AuditPage = Schemas['AuditPage'];
export type Workflow = Schemas['WorkflowOut'];
export type WorkflowSummary = Schemas['WorkflowSummary'];
export type WorkflowStatus = Schemas['WorkflowStatus'];
export type TriageResult = Schemas['TriageResult'];
export type KnowledgeDocument = Schemas['DocumentOut'];
export type KnowledgeAnswer = Schemas['KnowledgeAnswer'];
export type Citation = Schemas['Citation'];
export type Approval = Schemas['ApprovalOut'];
export type LedgerAccount = Schemas['AccountOut'];
export type LedgerTransaction = Schemas['TransactionOut'];
export type WorkflowResult = Schemas['WorkflowResult'];
export type Ticket = Schemas['TicketOut'];

/** Error body FastAPI returns for HTTPException: {"detail": "..."} */
export interface ApiError {
  detail: string | { msg: string }[];
}
