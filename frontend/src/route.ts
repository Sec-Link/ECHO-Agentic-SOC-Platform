export type RouteKey =
  | 'dashboard'
  | 'alerts'
  | 'tickets'
  | 'assets'
  | 'integrations'
  | 'dashboards'
  | 'datasources'
  | 'orchestrator'
  | 'interfaces'
  | 'correlation'
  | 'permissions'
  | 'registration-approvals'
  | 'audit-logs'
  | 'workflows'
  | 'workflow-executions'
  | 'ai-assistant'
  | 'profile';

export const permissionByKey: Record<RouteKey, string | undefined> = {
  dashboard: undefined,
  alerts: 'es_integration.view_alert',
  tickets: 'tickets.view_eventticket',
  assets: 'cmdb.view_asset',
  integrations: 'integrations.view_integration',
  dashboards: 'dashboards.view_dashboard',
  datasources: 'datasource.view_datasource',
  orchestrator: 'orchestrator.view_task',
  interfaces: 'workflow_interfaces.view_interfaceendpoint',
  correlation: 'correlation.view_correlationpolicy',
  permissions: 'accounts.view_user',
  'registration-approvals': 'accounts.view_user',
  'audit-logs': 'accounts.view_user',
  workflows: 'workflows.view_workflow',
  'workflow-executions': 'workflows.view_workflowexecution',
  profile: undefined,
  'ai-assistant': undefined,
};

export const keyToPath: Record<RouteKey, string> = {
  dashboard: '/dashboard',
  alerts: '/alerts',
  tickets: '/tickets',
  assets: '/cmdb/assets',
  integrations: '/settings/integrations',
  dashboards: '/settings/dashboards',
  datasources: '/settings/datasources',
  orchestrator: '/settings/orchestrator',
  interfaces: '/settings/interfaces',
  correlation: '/settings/correlation',
  permissions: '/settings/permissions',
  'registration-approvals': '/settings/registration-approvals',
  'audit-logs': '/settings/audit-logs',
  workflows: '/settings/workflows',
  'workflow-executions': '/settings/workflows/executions',
  'ai-assistant': '/settings/ai-assistant',
  profile: '/profile',
};

export function normalizePath(pathname: string): string {
  if (!pathname) return '/dashboard';
  if (pathname === '/') return '/dashboard';
  return pathname;
}

export function resolveRouteKey(pathname: string): { key: RouteKey; ticketNumber?: string } {
  const p = normalizePath(pathname);
  if (p === '/dashboard') return { key: 'dashboard' };
  if (p === '/alerts') return { key: 'alerts' };
  if (p === '/tickets') return { key: 'tickets' };
  if (p === '/cmdb/assets') return { key: 'assets' };
  if (p.startsWith('/tickets/')) {
    const parts = p.split('/').filter(Boolean);
    const ticketNumber = parts.length >= 2 ? decodeURIComponent(parts[1]) : '';
    return { key: 'tickets', ticketNumber };
  }

  if (p === '/settings/integrations') return { key: 'integrations' };
  if (p === '/settings/dashboards') return { key: 'dashboards' };
  if (p === '/settings/datasources') return { key: 'datasources' };
  if (p === '/settings/orchestrator') return { key: 'orchestrator' };
  if (p === '/settings/interfaces') return { key: 'interfaces' };
  if (p === '/settings/correlation') return { key: 'correlation' };
  if (p === '/settings/permissions') return { key: 'permissions' };
  if (p === '/settings/registration-approvals') return { key: 'registration-approvals' };
  if (p === '/settings/audit-logs') return { key: 'audit-logs' };
  if (p === '/settings/workflows') return { key: 'workflows' };
  if (p === '/settings/workflows/executions') return { key: 'workflow-executions' };
  if (p === '/settings/ai-assistant') return { key: 'ai-assistant' };
  if (p === '/profile') return { key: 'profile' };

  // Aliases
  if (p === '/list') return { key: 'tickets' };
  if (p.startsWith('/list/')) {
    const parts = p.split('/').filter(Boolean);
    const ticketNumber = parts.length >= 2 ? decodeURIComponent(parts[1]) : '';
    return { key: 'tickets', ticketNumber };
  }

  return { key: 'dashboard' };
}
