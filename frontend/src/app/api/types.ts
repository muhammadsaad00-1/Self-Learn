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
export type Chat = Schemas['ChatOut'];
export type ChatMessage = Schemas['MessageOut'];
export type Conversation = Schemas['ConversationOut'];
export type OpenedChat = Schemas['OpenedChat'];
export type Insights = Schemas['Insights'];
export type CustomerMe = Schemas['CustomerMe'];
export type LoginStartOut = Schemas['LoginStartOut'];
export type BankOption = Schemas['BankOption'];
export type CustomerSession = Schemas['CustomerSession'];
export type RiskAssessment = Schemas['RiskAssessment'];
export type BusinessProfile = Schemas['BusinessProfile'];
export type Industry = Schemas['Industry'];
export type OrgProfile = Schemas['OrgProfileOut'];
export type InboundAddress = Schemas['InboundAddress'];
export type DisputeAssessment = Schemas['DisputeAssessment'];
export type ImportTemplate = Schemas['TemplateOut'];
export type ImportField = Schemas['FieldOut'];
export type ImportBatch = Schemas['BatchOut'];
export type ImportBatchDetail = Schemas['BatchDetail'];
export type ImportRow = Schemas['RowOut'];
export type ImportCommit = Schemas['CommitOut'];

/** Error body FastAPI returns for HTTPException: {"detail": "..."} */
export interface ApiError {
  detail: string | { msg: string; loc?: (string | number)[] }[];
}
