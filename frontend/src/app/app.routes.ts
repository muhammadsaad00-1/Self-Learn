import { Routes } from '@angular/router';

import { adminGuard, authGuard } from './core/guards';

// Pages are lazy-loaded (loadComponent): each becomes its own small JS chunk,
// downloaded only when first visited.
export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./pages/auth-page').then((m) => m.AuthPage),
    data: { mode: 'login' },
  },
  {
    path: 'signup',
    loadComponent: () => import('./pages/signup-wizard').then((m) => m.SignupWizard),
  },
  // The customer's app: its own login (phone + one-time code) and its own session; never
  // the staff dashboard. /chat#<token> is the staff-issued link into the same app.
  {
    path: 'customer/login',
    loadComponent: () => import('./pages/customer-login-page').then((m) => m.CustomerLoginPage),
  },
  {
    path: 'customer',
    loadComponent: () => import('./pages/customer-portal-page').then((m) => m.CustomerPortalPage),
  },
  {
    path: 'chat',
    loadComponent: () => import('./pages/customer-portal-page').then((m) => m.CustomerPortalPage),
  },
  {
    path: '',
    loadComponent: () => import('./layout/shell').then((m) => m.Shell),
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'insights' },
      {
        path: 'insights',
        loadComponent: () => import('./pages/insights-page').then((m) => m.InsightsPage),
      },
      {
        path: 'chats',
        loadComponent: () => import('./pages/conversations-page').then((m) => m.ConversationsPage),
      },
      {
        path: 'workflows',
        loadComponent: () => import('./pages/workflows-page').then((m) => m.WorkflowsPage),
      },
      {
        path: 'knowledge',
        loadComponent: () => import('./pages/knowledge-page').then((m) => m.KnowledgePage),
      },
      {
        path: 'members',
        loadComponent: () => import('./pages/members-page').then((m) => m.MembersPage),
      },
      {
        path: 'profile',
        loadComponent: () => import('./pages/profile-page').then((m) => m.ProfilePage),
      },
      {
        path: 'tickets',
        loadComponent: () => import('./pages/tickets-page').then((m) => m.TicketsPage),
      },
      {
        path: 'bookings',
        loadComponent: () => import('./pages/bookings-page').then((m) => m.BookingsPage),
        canActivate: [adminGuard],
      },
      {
        path: 'approvals',
        loadComponent: () => import('./pages/approvals-page').then((m) => m.ApprovalsPage),
        canActivate: [adminGuard],
      },
      {
        path: 'data',
        loadComponent: () => import('./pages/data-page').then((m) => m.DataPage),
        canActivate: [adminGuard],
      },
      {
        path: 'settings',
        loadComponent: () => import('./pages/settings-page').then((m) => m.SettingsPage),
        canActivate: [adminGuard],
      },
      {
        path: 'audit',
        loadComponent: () => import('./pages/audit-page').then((m) => m.AuditPage),
        canActivate: [adminGuard],
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
