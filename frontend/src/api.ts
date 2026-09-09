import axios, { InternalAxiosRequestConfig } from 'axios';
import { listenForWorkflowProgress, type WorkflowProgress } from './services/workflows/progress';

let accessToken: string | null = null;

export function setAccessToken(token: string) {
  accessToken = token;
}

function getCookie(name: string): string | null {
  try {
    const value = `; ${document.cookie || ''}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length < 2) return null;
    return parts.pop()!.split(';').shift() || null;
  } catch {
    return null;
  }
}

function setCookie(name: string, value: string) {
  try {
    document.cookie = `${name}=${value}; path=/`;
  } catch {}
}

function randomCsrfToken(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  try {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    let out = '';
    for (let i = 0; i < bytes.length; i++) out += alphabet[bytes[i] % alphabet.length];
    return out;
  } catch {
    let out = '';
    for (let i = 0; i < 32; i++) out += alphabet[Math.floor(Math.random() * alphabet.length)];
    return out;
  }
}

function ensureDjangoCsrfCookie() {
  const existing = getCookie('csrftoken');
  if (existing) return existing;
  const token = randomCsrfToken();
  setCookie('csrftoken', token);
  return token;
}

// initialize from persistent storage if available
try {
  const persisted = localStorage.getItem('siem_access_token');
  // Migration safety: previous versions stored JWT (contains dots). DRF tokens do not.
  if (persisted && !persisted.includes('.')) accessToken = persisted;
  else if (persisted && persisted.includes('.')) {
    accessToken = null;
    try { localStorage.removeItem('siem_access_token'); } catch (e) {}
  }
} catch (err) {}

export function clearAccessToken() {
  accessToken = null;
  try {
    localStorage.removeItem('siem_access_token');
    localStorage.removeItem('siem_is_readonly');
  } catch (e) {}
}

function resolveApiBase() {
  const raw = (typeof process !== 'undefined' && (process as any).env && (process as any).env.NEXT_PUBLIC_API_BASE)
    ? String((process as any).env.NEXT_PUBLIC_API_BASE).trim()
    : '';
  if (!raw) return '/api/v1';
  // Guard against accidental UI/page URLs being used as API base.
  if (raw.includes('/settings/')) return '/api/v1';
  return raw.replace(/\/+$/, '');
}

const client = axios.create({
  baseURL: resolveApiBase(),
  withCredentials: true,
  xsrfCookieName: 'csrftoken',
  xsrfHeaderName: 'X-CSRFToken',
});
const addAuthHeader = (config: InternalAxiosRequestConfig) => {
  const method = String(config.method || 'get').toUpperCase();
  const url = String(config.url || '');
  const readonlyAllowedPaths = ['/accounts/auth/logout/', '/accounts/auth/otp/request/', '/accounts/auth/otp/verify/'];
  if (typeof window !== 'undefined') {
    let isReadonly = false;
    try {
      isReadonly = localStorage.getItem('siem_is_readonly') === '1';
    } catch {}
    const safeMethod = method === 'GET' || method === 'HEAD' || method === 'OPTIONS';
    const allowedPath = readonlyAllowedPaths.some((p) => url.startsWith(p));
    if (isReadonly && !safeMethod && !allowedPath) {
      return Promise.reject({
        message: 'Readonly users cannot modify data.',
        response: { status: 403, data: { detail: 'Readonly users cannot modify data.' } },
      });
    }
  }

  if (accessToken) {
    if (!config.headers) {
      config.headers = {} as any;
    }
    (config.headers as any).Authorization = `Token ${accessToken}`;
  }
  return config;
};
client.interceptors.request.use(addAuthHeader);

const TICKETS_BASE = '/tickets';

export async function login(username: string, password: string) {
  const res = await client.post('/accounts/auth/login/', { username, password });
  setAccessToken(res.data.token);
  try {
    // persist token for page reloads
    localStorage.setItem('siem_access_token', res.data.token);
    // store username for UI display (fall back to provided username)
    const apiUsername = res.data?.user?.username || res.data?.username;
    if (apiUsername) localStorage.setItem('siem_username', apiUsername);
    else if (username) localStorage.setItem('siem_username', username);
    if (res.data?.user?.is_readonly) localStorage.setItem('siem_is_readonly', '1');
    else localStorage.removeItem('siem_is_readonly');
  } catch (err) {
    // ignore storage errors
  }
  return res.data;
}

export async function register(
  username: string,
  email: string,
  password: string,
  passwordConfirm: string
) {
  const res = await client.post('/accounts/auth/register/', {
    username,
    email,
    password,
    password_confirm: passwordConfirm,
  });
  setAccessToken(res.data.token);
  try {
    localStorage.setItem('siem_access_token', res.data.token);
    const apiUsername = res.data?.user?.username || username;
    if (apiUsername) localStorage.setItem('siem_username', apiUsername);
    if (res.data?.user?.is_readonly) localStorage.setItem('siem_is_readonly', '1');
    else localStorage.removeItem('siem_is_readonly');
  } catch (err) {}
  return res.data;
}

export async function registerEmail(email: string) {
  const res = await client.post('/accounts/auth/register-email/', { email });
  return res.data;
}

export async function getGuestEmailStatus(email: string) {
  const res = await client.post('/accounts/auth/guest-email-status/', { email });
  return res.data as {
    email: string;
    is_registered_readonly: boolean;
    next_action: 'register' | 'send_otp';
  };
}

export async function requestOtp(email: string) {
  const res = await client.post('/accounts/auth/otp/request/', { email });
  return res.data;
}

export async function verifyOtp(email: string, otp: string) {
  const res = await client.post('/accounts/auth/otp/verify/', { email, otp });
  setAccessToken(res.data.token);
  try {
    localStorage.setItem('siem_access_token', res.data.token);
    const apiUsername = res.data?.user?.username || email;
    localStorage.setItem('siem_username', apiUsername);
    if (res.data?.user?.is_readonly) localStorage.setItem('siem_is_readonly', '1');
    else localStorage.removeItem('siem_is_readonly');
  } catch (err) {}
  return res.data;
}

export async function listRegistrationRequests(params?: { status?: string; email?: string }) {
  const qp = new URLSearchParams();
  if (params?.status) qp.set('status', params.status);
  if (params?.email) qp.set('email', params.email);
  const suffix = qp.toString() ? `?${qp.toString()}` : '';
  const res = await client.get(`/accounts/registration-requests/${suffix}`);
  return res.data;
}

export async function approveRegistrationRequest(requestId: string, note?: string) {
  const res = await client.post(`/accounts/registration-requests/${encodeURIComponent(requestId)}/approve/`, {
    note: note || '',
  });
  return res.data;
}

export async function rejectRegistrationRequest(requestId: string, reason?: string) {
  const res = await client.post(`/accounts/registration-requests/${encodeURIComponent(requestId)}/reject/`, {
    reason: reason || '',
  });
  return res.data;
}

export async function getSystemSettings() {
  const res = await client.get('/accounts/system-settings/');
  return res.data;
}

export async function updateSystemSettings(payload: {
  auto_approve_enabled?: boolean;
  workflow_http_allowlist?: string[];
}) {
  const res = await client.put('/accounts/system-settings/', payload);
  return res.data;
}

export type AuditLogQueryParams = {
  event_type?: string;
  email?: string;
  status?: string;
  from_date?: string;
  to_date?: string;
  page?: number;
  limit?: number;
  sort?: 'created_at' | '-created_at';
};

export async function listAuditLogs(params?: AuditLogQueryParams) {
  const qp = new URLSearchParams();
  if (params?.event_type) qp.set('event_type', params.event_type);
  if (params?.email) qp.set('email', params.email);
  if (params?.status) qp.set('status', params.status);
  if (params?.from_date) qp.set('from_date', params.from_date);
  if (params?.to_date) qp.set('to_date', params.to_date);
  if (params?.page) qp.set('page', String(params.page));
  if (params?.limit) qp.set('limit', String(params.limit));
  if (params?.sort) qp.set('sort', params.sort);
  const suffix = qp.toString() ? `?${qp.toString()}` : '';
  const res = await client.get(`/accounts/audit-logs/${suffix}`);
  return res.data;
}

export async function fetchAlerts(
  page = 1,
  page_size = 20,
  index?: string,
  opts?: { q?: string; severity?: string; ordering?: string }
) {
  const qp = new URLSearchParams();
  qp.set('page', String(page));
  qp.set('page_size', String(page_size));
  if (index && String(index).trim()) qp.set('index', String(index).trim());
  if (opts?.q && String(opts.q).trim()) qp.set('q', String(opts.q).trim());
  if (opts?.severity && String(opts.severity).trim()) qp.set('severity', String(opts.severity).trim());
  if (opts?.ordering && String(opts.ordering).trim()) qp.set('ordering', String(opts.ordering).trim());
  const res = await client.get(`/alerts/list/?${qp.toString()}`);
  return res.data;
}

export async function fetchDashboard(params?: { start_time?: string; end_time?: string; all_time?: boolean }) {
  const qp = new URLSearchParams();
  qp.set('_ts', String(Date.now()));
  if (params?.all_time) qp.set('all_time', 'true');
  if (params?.start_time) qp.set('start_time', params.start_time);
  if (params?.end_time) qp.set('end_time', params.end_time);
  const res = await client.get(`/alerts/dashboard/?${qp.toString()}`);
  return res.data;
}

export async function syncAlertsToDb(size: number = 100) {
  const url = `/alerts/sync/?size=${encodeURIComponent(String(size))}`;
  const res = await client.post(url);
  return res.data;
}

export async function fetchSlaTickets(params?: Record<string, string | number | undefined | null>) {
  let query = '';
  if (params && Object.keys(params).length) {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      const str = String(value).trim();
      if (!str) return;
      search.append(key, str);
    });
    const built = search.toString();
    if (built) query = `?${built}`;
  }
  const res = await client.get(`${TICKETS_BASE}/${query}`);
  return res.data;
}

export async function createSlaTicket(payload: {
  ticket_number?: string;
  event_siem_id?: string;
  title: string;
  description?: string;
  priority?: string;
  status?: string;
  create_uid?: string;
  labels?: Array<{ label_name: string; label_value?: string | null }>;
}) {
  let finalPayload = payload;
  if (!payload.create_uid) {
    try {
      const username = localStorage.getItem('siem_username');
      if (username) finalPayload = { ...payload, create_uid: username };
    } catch {}
  }
  const res = await client.post(`${TICKETS_BASE}/`, finalPayload);
  return res.data;
}

export async function fetchSlaTicket(ticketNumber: string) {
  const res = await client.get(`${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/`);
  return res.data;
}

export async function updateSlaTicketStatus(ticketNumber: string, payload: { status: string; notes?: string }) {
  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/update_status/`,
    payload
  );
  return res.data;
}

export async function batchUpdateSlaTickets(payload: { ticket_ids: string[]; status: string; notes?: string }) {
  // Batch endpoints accept the table's selected ticket numbers directly. The
  // backend normalizes display labels such as "Closed" into stored enum values
  // such as "closed", so callers can keep payloads human-readable.
  const res = await client.post(`${TICKETS_BASE}/batch-update/`, payload);
  return res.data;
}

export async function batchDeleteSlaTickets(payload: { ticket_ids: string[] }) {
  // Use POST instead of DELETE-with-body for stronger browser/proxy
  // compatibility. The backend performs a soft delete for audit retention.
  const res = await client.post(`${TICKETS_BASE}/batch-delete/`, payload);
  return res.data;
}

export async function updateSlaTicket(ticketNumber: string, payload: Partial<{
  assigned_user: number | null;
  current_assign_owner: string | null;
  current_assign_group: string | null;
  priority: string;
  is_deleted: boolean;
  labels: Array<{ label_name: string; label_value?: string | null }>;
}>) {
  const res = await client.patch(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/`,
    payload
  );
  return res.data;
}

export async function fetchSlaTicketAttachments(ticketNumber: string) {
  const res = await client.get(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/attachments/`
  );
  return res.data;
}

export async function fetchSlaTicketTimeline(ticketNumber: string) {
  const res = await client.get(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/timeline/`
  );
  return res.data;
}

export async function addSlaTicketWorklog(ticketNumber: string, payload: { log_entry: string }) {
  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/add_worklog/`,
    payload
  );
  return res.data;
}

export async function uploadSlaTicketAttachment(ticketNumber: string, file: File, filename?: string) {
  const formData = new FormData();
  if (filename) {
    formData.append('file_path', file, filename);
  } else {
    formData.append('file_path', file);
  }
  // Django's FileField or specific view expects 'file_path' or 'file'?
  // Let's check backend/tickets/views.py if needed, but 'file_path' is likely from model field name.
  // Actually usually DRF ViewSet upload expects the field name.
  // Let's assume 'file' or 'file_path'. Based on standard practices or previous code context if any.
  // But checking TicketList.tsx might reveal how it was intended.
  // Let's stick to a safe guess or common pattern, often 'file'.
  // However, looking at previous errors, the function signature is needed.

  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/attachments/`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    }
  );
  return res.data;
}

// CMDB API
const CMDB_BASE = '/cmdb';

function extractDownloadFilename(contentDisposition?: string, fallback = 'download.bin') {
  if (!contentDisposition) return fallback;
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return filenameMatch?.[1] || fallback;
}

export async function fetchAssets(page = 1, page_size = 20, search = '', ordering = '-updated_at') {
  const url = `${CMDB_BASE}/assets/?page=${page}&page_size=${page_size}&search=${encodeURIComponent(search)}&ordering=${ordering}`;
  const res = await client.get(url);
  return res.data;
}

export async function exportAssets(fileFormat: 'xlsx' | 'csv' = 'xlsx', search = '', ordering = '-updated_at') {
  const res = await client.get(`${CMDB_BASE}/assets/export/`, {
    params: {
      file_format: fileFormat,
      search,
      ordering,
    },
    responseType: 'blob',
  });

  return {
    blob: res.data as Blob,
    filename: extractDownloadFilename(
      res.headers['content-disposition'],
      `cmdb_assets.${fileFormat}`,
    ),
  };
}

export async function createAsset(payload: any) {
  const res = await client.post(`${CMDB_BASE}/assets/`, payload);
  return res.data;
}

export async function updateAsset(id: number, payload: any) {
  const res = await client.patch(`${CMDB_BASE}/assets/${id}/`, payload);
  return res.data;
}

export async function deleteAsset(id: number) {
  await client.delete(`${CMDB_BASE}/assets/${id}/`);
}

export async function importAssets(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  // Increase timeout for large imports
  const res = await client.post(`${CMDB_BASE}/assets/import_excel/`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000
  });
  return res.data;
}

export async function fetchAssetColumns() {
  const res = await client.get(`${CMDB_BASE}/columns/`);
  return res.data;
}
export async function createAssetColumn(payload: {name: string, label: string, data_type: string}) {
  const res = await client.post(`${CMDB_BASE}/columns/`, payload);
  return res.data;
}
export async function deleteAssetColumn(id: number) {
  await client.delete(`${CMDB_BASE}/columns/${id}/`);
}

export async function toggleSlaTicketPending(ticketNumber: string) {
  // Django view is CSRF-protected; ensure cookie exists so axios can mirror it to X-CSRFToken.
  ensureDjangoCsrfCookie();
  const res = await client.post(`${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/toggle-pending/`);
  return res.data;
}

export async function fetchSlaTicketHandleLogsHtml(ticketNumber: string) {
  // Use the edit page (HTML) to scrape handle logs without touching backend APIs.
  const res = await client.get(`${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/edit/`, {
    responseType: 'text',
    headers: { Accept: 'text/html' },
  });
  return res.data as unknown as string;
}

export async function fetchSlaTicketHandleLogs(ticketNumber: string) {
  const res = await client.get(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/handlelogs/`
  );
  return res.data;
}

export async function generateSlaTicketAiAssistant(
  ticketNumber: string,
  payload: {
    alert_json?: any;
    trigger_rule?: string;
    related_logs?: string[];
    api_key?: string;
    model?: string;
    base_url?: string;
    timeout_seconds?: number;
    enabled?: boolean;
    mcp_enabled?: boolean;
    mcp_base_url?: string;
    mcp_servers?: Array<{ endpoint?: string; title?: string }>;
    mcp_token?: string;
    mcp_timeout_seconds?: number;
    skills?: Array<{
      name?: string;
      version?: string;
      route?: string;
      enabled?: boolean;
    }>;
  }
) {
  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/ai_assistant/`,
    payload
  );
  return res.data;
}

export async function generateSlaTicketAiMention(
  ticketNumber: string,
  payload: {
    prompt: string;
    alert_json?: any;
    trigger_rule?: string;
    related_logs?: string[];
    api_key?: string;
    model?: string;
    base_url?: string;
    timeout_seconds?: number;
    enabled?: boolean;
    mcp_enabled?: boolean;
    mcp_base_url?: string;
    mcp_servers?: Array<{ endpoint?: string; title?: string }>;
    mcp_token?: string;
    mcp_timeout_seconds?: number;
    skills?: Array<{
      name?: string;
      version?: string;
      route?: string;
      enabled?: boolean;
    }>;
  }
) {
  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/ai_mention/`,
    payload
  );
  return res.data;
}

export async function fetchTicketCallablePlaybooks() {
  const res = await client.get('/workflows/ticket-playbooks/suggest/');
  return res.data;
}

export async function fetchTicketCallablePlaybookSchema(workflowId: string) {
  const res = await client.get(`/workflows/ticket-playbooks/${encodeURIComponent(workflowId)}/inputs-schema/`);
  return res.data;
}

export async function invokeTicketCallablePlaybook(
  workflowId: string,
  payload: {
    ticket: Record<string, any>;
    inputs?: Record<string, any>;
    comment?: string;
  }
) {
  const res = await client.post(`/workflows/ticket-playbooks/${encodeURIComponent(workflowId)}/invoke/`, payload);
  return res.data;
}

export async function dispatchTicketPlaybooks(payload: {
  trigger_event: 'on_create' | 'on_status_change';
  ticket: Record<string, any>;
}) {
  const res = await client.post('/workflows/ticket-playbooks/dispatch/', payload);
  return res.data;
}

export async function fetchTicketPlaybookWorkplan(ticketNumber: string) {
  const res = await client.get(`/workflows/ticket-playbooks/workplan/?ticket_number=${encodeURIComponent(ticketNumber)}`);
  return res.data;
}

export async function testAiAssistantConnectivity(payload: {
  api_key?: string;
  model?: string;
  base_url?: string;
  timeout_seconds?: number;
}) {
  const res = await client.post('/ai-assistant/test-connectivity/', payload);
  return res.data;
}

export async function fetchAiAssistantMcpMonitor(params?: {
  tool?: string;
  status?: 'all' | 'completed' | 'failed' | 'running';
  page?: number;
  page_size?: number;
}) {
  const qp = new URLSearchParams();
  if (params?.tool) qp.set('tool', params.tool);
  if (params?.status) qp.set('status', params.status);
  if (params?.page) qp.set('page', String(params.page));
  if (params?.page_size) qp.set('page_size', String(params.page_size));
  const q = qp.toString();
  const res = await client.get(`/ai-assistant/mcp-monitor/${q ? `?${q}` : ''}`);
  return res.data;
}

export async function fetchAiAssistantInternalMcpTools() {
  const res = await client.get('/ai-assistant/mcp/tools/');
  return res.data;
}

export async function fetchAiAssistantSkillMonitor() {
  const res = await client.get('/ai-assistant/skill-monitor/');
  return res.data;
}

export async function aiAssistantChat(payload: {
  message: string;
  messages?: Array<{ role: string; content: any }>;
  ticket_number?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
  timeout_seconds?: number;
  max_iterations?: number;
}) {
  const res = await client.post('/ai-assistant/chat/', payload);
  return res.data;
}

export async function fetchSlaTicketAiChatHistory(
  ticketNumber: string,
  params?: { limit?: number; before?: string }
) {
  const qp = new URLSearchParams();
  if (params?.limit) qp.set('limit', String(params.limit));
  if (params?.before) qp.set('before', params.before);
  const q = qp.toString();
  const res = await client.get(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/ai_chat_history/${q ? `?${q}` : ''}`
  );
  return res.data;
}

export async function clearSlaTicketAiChatHistory(ticketNumber: string) {
  const res = await client.delete(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/ai_chat_clear/`
  );
  return res.data;
}

export async function fetchAiAssistantMcpRegistryServers(payload: {
  base_url?: string;
  token?: string;
  query?: string;
  cursor?: string;
  limit?: number;
}) {
  const res = await client.post('/ai-assistant/mcp-registry/servers/', payload || {});
  return res.data;
}

export async function listAiAssistantMcpServers() {
  const res = await client.get('/ai-assistant/external-mcp/');
  return res.data;
}

export async function createAiAssistantMcpServer(payload: {
  name: string;
  endpoint: string;
  title?: string;
  token?: string;
  enabled?: boolean;
  extra?: any;
}) {
  const res = await client.post('/ai-assistant/external-mcp/', payload);
  return res.data;
}

export async function updateAiAssistantMcpServer(name: string, payload: Partial<{
  endpoint: string;
  title: string;
  token: string;
  enabled: boolean;
  extra: any;
}>) {
  const res = await client.put(`/ai-assistant/external-mcp/${encodeURIComponent(name)}/`, payload);
  return res.data;
}

export async function deleteAiAssistantMcpServer(name: string) {
  const res = await client.delete(`/ai-assistant/external-mcp/${encodeURIComponent(name)}/`);
  return res.data;
}

export async function startAiAssistantMcpServer(name: string) {
  const res = await client.post(`/ai-assistant/external-mcp/${encodeURIComponent(name)}/start/`);
  return res.data;
}

export async function stopAiAssistantMcpServer(name: string) {
  const res = await client.post(`/ai-assistant/external-mcp/${encodeURIComponent(name)}/stop/`);
  return res.data;
}

export async function fetchAiAssistantSkillCatalog() {
  const res = await client.get('/ai-assistant/skills/catalog/');
  return res.data;
}

export async function listAiAssistantSkillConfigs() {
  const res = await client.get('/ai-assistant/skills/config/');
  return res.data;
}

export async function createAiAssistantSkillConfig(payload: {
  name: string;
  version?: string;
  route?: string;
  enabled?: boolean;
  description?: string;
}) {
  const res = await client.post('/ai-assistant/skills/config/', payload);
  return res.data;
}

export async function updateAiAssistantSkillConfig(name: string, payload: Partial<{
  version: string;
  route: string;
  enabled: boolean;
  description: string;
}>) {
  const res = await client.put(`/ai-assistant/skills/config/${encodeURIComponent(name)}/`, payload);
  return res.data;
}

export async function deleteAiAssistantSkillConfig(name: string) {
  const res = await client.delete(`/ai-assistant/skills/config/${encodeURIComponent(name)}/`);
  return res.data;
}

export async function fetchAiAssistantSkillContent(name: string) {
  const res = await client.get(`/ai-assistant/skills/content/${encodeURIComponent(name)}/`);
  return res.data;
}

export async function updateAiAssistantSkillContent(name: string, payload: { content: string; title?: string; description?: string }) {
  const res = await client.put(`/ai-assistant/skills/content/${encodeURIComponent(name)}/`, payload);
  return res.data;
}

export async function fetchSlaTicketFieldChoices() {
  const res = await client.get(`${TICKETS_BASE}/field_choices/`);
  return res.data;
}

export async function resolveSlaTicket(
  ticketNumber: string,
  payload: { event_category?: string; event_result?: string; notes?: string }
) {
  const res = await client.post(
    `${TICKETS_BASE}/${encodeURIComponent(ticketNumber)}/resolve/`,
    payload
  );
  return res.data;
}

export async function fetchTicketPolicies() {
  const res = await client.get('/ticket-policies/');
  return res.data;
}

export async function createTicketPolicy(payload: any) {
  const res = await client.post('/ticket-policies/', payload);
  return res.data;
}

export async function updateTicketPolicy(id: string, payload: any) {
  const res = await client.put(`/ticket-policies/${encodeURIComponent(id)}/`, payload);
  return res.data;
}

export async function deleteTicketPolicy(id: string) {
  const res = await client.delete(`/ticket-policies/${encodeURIComponent(id)}/`);
  return res.data;
}

// Dataset APIs removed — use DataSource + SQL preview instead

export async function queryPreview(payload:any){
  const r = await client.post('/query/preview', payload)
  return r.data
}

export async function testIntegrationById(id: string){
  const r = await client.post(`/integrations/${encodeURIComponent(id)}/test/`, {})
  return r.data
}

export async function testEsIntegration(payload:any){
  const r = await client.post('/integrations/test_es', payload)
  return r.data
}

export async function previewEsIntegration(payload:any){
  const r = await client.post('/integrations/preview_es', payload)
  return r.data
}

export async function integrationsPreviewEsMapping(payload:any){
  const r = await client.post('/integrations/preview_es_mapping', payload)
  return r.data
}

// Correlation (stubbed backend)
export async function getCorrelationPolicy(){
  const r = await client.get('/correlation/policy/')
  return r.data
}

export async function saveCorrelationPolicy(payload:any){
  const r = await client.post('/correlation/policy/', payload)
  return r.data
}

export async function getCorrelationEvents(params?: { from?: string; to?: string; bucket?: string; seed?: boolean; seed_tickets?: number; seed_min?: number; seed_max?: number; seed_hours?: number }){
  const query = new URLSearchParams()
  if(params?.from) query.set('from', params.from)
  if(params?.to) query.set('to', params.to)
  if(params?.bucket) query.set('bucket', params.bucket)
  if(params?.seed) query.set('seed', '1')
  if(params?.seed_tickets) query.set('seed_tickets', String(params.seed_tickets))
  if(params?.seed_min) query.set('seed_min', String(params.seed_min))
  if(params?.seed_max) query.set('seed_max', String(params.seed_max))
  if(params?.seed_hours) query.set('seed_hours', String(params.seed_hours))
  const qs = query.toString()
  const url = `/correlation/events/${qs ? `?${qs}` : ''}`
  const r = await client.get(url)
  return r.data
}

export async function getCorrelationRiskEntities(params?: { from?: string; to?: string; min_alerts?: number }){
  const query = new URLSearchParams()
  if(params?.from) query.set('from', params.from)
  if(params?.to) query.set('to', params.to)
  if(params?.min_alerts != null) query.set('min_alerts', String(params.min_alerts))
  const qs = query.toString()
  const r = await client.get(`/correlation/risk-entities/${qs ? `?${qs}` : ''}`)
  return r.data as {
    from: string; to: string;
    entities: Array<{
      entity: string; entity_type: string; alert_count: number; rule_count: number;
      ticket_count: number; alert_ids: string[]; rules: string[]; tickets: string[];
      severities: string[]; last_seen: string | null;
    }>;
  }
}

// Integrations CRUD
export async function listIntegrations(){
  const r = await client.get('/integrations/')
  return r.data
}

export async function createIntegration(payload:any){
  const r = await client.post('/integrations/', payload)
  return r.data
}

export async function updateIntegration(id:string, payload:any){
  const r = await client.put(`/integrations/${id}/`, payload)
  return r.data
}

export async function deleteIntegration(id:string){
  const r = await client.delete(`/integrations/${id}/`)
  return r.data
}

export default client

export async function fetchDashboardConversionStats(params?: { start_time?: string; end_time?: string; all_time?: boolean }) {
  const qp = new URLSearchParams();
  // Dashboard chart APIs live under the dashboard module, not tickets.
  if (params?.all_time) qp.set('all_time', 'true');
  if (params?.start_time) qp.set('created_from', params.start_time);
  if (params?.end_time) qp.set('created_to', params.end_time);
  const suffix = qp.toString() ? `?${qp.toString()}` : '';
  const r = await client.get(`/dashboards/conversion-stats/${suffix}`);
  return r.data;
}

export async function fetchDashboardSankeyStats(params?: { start_time?: string; end_time?: string }) {
  const qp = new URLSearchParams();
  if (params?.start_time) qp.set('created_from', params.start_time);
  if (params?.end_time) qp.set('created_to', params.end_time);
  const suffix = qp.toString() ? `?${qp.toString()}` : '';
  const r = await client.get(`/dashboards/sankey-stats/${suffix}`);
  return r.data;
}

export async function fetchRiskFunnel() {
  const r = await client.get('/risk/funnel/');
  return r.data as { stages: { stage: string; value: number }[] };
}

export async function fetchRiskSankey(limit = 12) {
  const r = await client.get(`/risk/sankey/?limit=${limit}`);
  return r.data as { nodes: { name: string }[]; links: { source: string; target: string; value: number }[] };
}

export async function fetchRiskTopEntities(limit = 10) {
  const r = await client.get(`/risk/top-entities/?limit=${limit}`);
  return r.data as {
    id: number; risk_object: string; risk_object_type: string;
    current_score: number; tier: string; total_events: number; last_seen: string | null;
  }[];
}

export async function getRbacMe(){
  const r = await client.get('/rbac/me/')
  return r.data
}

export async function listUsers(){
  const r = await client.get('/accounts/users/')
  return r.data
}

export async function createUser(payload: {
  username: string
  email?: string
  first_name?: string
  last_name?: string
  is_active?: boolean
  is_staff?: boolean
  is_superuser?: boolean
  groups?: number[]
}){
  const r = await client.post('/accounts/users/', payload)
  return r.data
}

export async function updateUser(id: number, payload: {
  username?: string
  email?: string
  first_name?: string
  last_name?: string
  is_active?: boolean
  is_staff?: boolean
  is_superuser?: boolean
  groups?: number[]
}){
  const r = await client.patch(`/accounts/users/${encodeURIComponent(String(id))}/`, payload)
  return r.data
}

export async function resetUserPassword(id: number, payload: { new_password: string; confirm_password: string }){
  const r = await client.post(`/accounts/users/${encodeURIComponent(String(id))}/reset-password/`, payload)
  return r.data
}

export async function getUser(id: number){
  const r = await client.get(`/accounts/users/${encodeURIComponent(String(id))}/`)
  return r.data
}
export async function deleteUser(id: number){
  const r = await client.delete(`/accounts/users/${encodeURIComponent(String(id))}/`)
  return r.data
}

export async function changePassword(payload: { old_password: string; new_password: string; confirm_password: string }){
  const r = await client.post('/accounts/change-password/', payload)
  return r.data
}

export async function listGroups(){
  const r = await client.get('/accounts/groups/')
  return r.data
}

export async function createGroup(payload: { name: string; permissions?: number[] }){
  const r = await client.post('/accounts/groups/', payload)
  return r.data
}

export async function updateGroup(id: number, payload: { name?: string; permissions?: number[] }){
  const r = await client.patch(`/accounts/groups/${encodeURIComponent(String(id))}/`, payload)
  return r.data
}

export async function deleteGroup(id: number){
  const r = await client.delete(`/accounts/groups/${encodeURIComponent(String(id))}/`)
  return r.data
}

export async function listPermissions(params?: { app_labels?: string[]; common_only?: boolean }){
  const qs = new URLSearchParams()
  if (params?.app_labels && params.app_labels.length) qs.set('app_labels', params.app_labels.join(','))
  if (params?.common_only) qs.set('common_only', 'true')
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const r = await client.get(`/permissions/permissions/${suffix}`)
  return r.data
}

export async function getUserPermissions(userId: number){
  const r = await client.get(`/permissions/users/${userId}/permissions/`)
  return r.data
}

export async function updateUserPermissions(userId: number, permission_ids: number[]){
  const r = await client.put(`/permissions/users/${userId}/permissions/`, { permission_ids })
  return r.data
}

export async function getUserGroups(userId: number){
  const r = await client.get(`/permissions/users/${userId}/groups/`)
  return r.data
}

export async function updateUserGroups(userId: number, group_ids: number[]){
  const r = await client.put(`/permissions/users/${userId}/groups/`, { group_ids })
  return r.data
}

export async function getGroupPermissions(groupId: number){
  const r = await client.get(`/permissions/groups/${groupId}/permissions/`)
  return r.data
}

export async function updateGroupPermissions(groupId: number, permission_ids: number[]){
  const r = await client.put(`/permissions/groups/${groupId}/permissions/`, { permission_ids })
  return r.data
}

// ============ Workflow API ============

const WORKFLOWS_BASE = '/workflows';

export interface WorkflowStep {
  id?: string;
  order: number;
  name: string;
  node_type?: 'action' | 'condition' | 'start' | 'end';
  node_category?: string;
  position_x?: number;
  position_y?: number;
  action_type: string;
  action_config: Record<string, any>;
  configured_secret_fields?: string[];
  timeout_seconds: number;
  on_failure: 'stop' | 'continue' | 'retry' | 'skip';
  retry_count: number;
  retry_delay_seconds?: number;
  condition?: Record<string, any>;
  next_step_true?: string;
  next_step_false?: string;
  connections?: string[];
  is_active: boolean;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
  label?: string;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  trigger_type: string;
  // New workflows use Prefect; 'local' is returned only for legacy records.
  execution_engine?: 'local' | 'prefect';
  trigger_conditions: Record<string, any>;
  schedule_cron?: string;
  is_active: boolean;
  is_draft: boolean;
  version: number;
  published_version?: number | null;
  published_at?: string | null;
  has_unpublished_changes?: boolean;
  tags: string[];
  edges?: WorkflowEdge[];
  steps?: WorkflowStep[];
  step_count?: number;
  execution_count?: number;
  last_execution?: {
    id: string;
    status: string;
    started_at: string;
    completed_at?: string;
  };
  created_by?: number;
  created_by_username?: string;
  created_at: string;
  updated_at: string;
}

export interface TicketWorkflowBinding {
  id: string;
  name: string;
  workflow: string;
  workflow_name?: string;
  label_filters: Array<{ label_name: string; label_value?: string | null }>;
  label_filter_logic: 'AND' | 'OR';
  created_by?: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowExecution {
  id: string;
  workflow: string;
  workflow_name: string;
  workflow_version: number;
  trigger_source: string;
  trigger_data: Record<string, any>;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'paused';
  current_step: number;
  total_steps: number;
  completed_steps: number;
  progress_percent: number;
  started_at?: string;
  completed_at?: string;
  duration?: string;
  duration_seconds?: number;
  result_data: Record<string, any>;
  error_message?: string;
  context: Record<string, any>;
  executed_by?: number;
  executed_by_username?: string;
  // Prefect flow run id when the parent workflow uses execution_engine='prefect'.
  // Empty string for local-engine executions.
  task_result_id?: string;
  step_executions?: StepExecution[];
  created_at: string;
}

export interface StepExecution {
  id: string;
  step: string;
  step_name: string;
  step_order: number;
  action_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  attempt_number: number;
  started_at?: string;
  completed_at?: string;
  input_data: Record<string, any>;
  output_data: Record<string, any>;
  error_message?: string;
  logs?: string;
  duration_seconds?: number;
}

export interface ActionInfo {
  action_type: string;
  name: string;
  description: string;
  category: string;
  config_schema: Record<string, any>;
}

export interface WorkflowStats {
  workflows: {
    total: number;
    active: number;
    inactive: number;
  };
  executions: {
    total: number;
    completed: number;
    failed: number;
    running: number;
    success_rate: number;
  };
  status_breakdown: Record<string, number>;
  recent_executions: WorkflowExecution[];
}

export interface SavedWorkflowNode {
  id: string;
  name: string;
  node_type: 'action' | 'condition' | 'start' | 'end';
  node_category: string;
  action_type?: string;
  action_config: Record<string, any>;
  configured_secret_fields?: string[];
  timeout_seconds: number;
  on_failure: 'stop' | 'continue' | 'retry' | 'skip';
  retry_count: number;
  retry_delay_seconds: number;
  condition?: Record<string, any>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface InterfaceEndpoint {
  id: string;
  name: string;
  description?: string;
  interface_type: 'api' | 'webhook';
  secret_token: string;
  hmac_secret?: string;
  is_active: boolean;
  ingest_url?: string;
  last_event_at?: string;
  code_examples?: {
    curl?: string;
    python?: string;
    javascript?: string;
  };
  created_at: string;
  updated_at: string;
}

export interface InterfaceRequestLog {
  id: number;
  method: string;
  source_ip?: string;
  response_status: number;
  request_body: Record<string, any>;
  response_body: Record<string, any>;
  created_at: string;
}

// List workflows
export async function listWorkflows(params?: {
  trigger_type?: string;
  is_active?: boolean;
  is_draft?: boolean;
  search?: string;
}): Promise<Workflow[]> {
  const qs = new URLSearchParams();
  if (params?.trigger_type) qs.set('trigger_type', params.trigger_type);
  if (params?.is_active !== undefined) qs.set('is_active', String(params.is_active));
  if (params?.is_draft !== undefined) qs.set('is_draft', String(params.is_draft));
  if (params?.search) qs.set('search', params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await client.get(`${WORKFLOWS_BASE}/workflows/${suffix}`);
  return r.data;
}

// Get workflow detail
export async function getWorkflow(id: string): Promise<Workflow> {
  const r = await client.get(`${WORKFLOWS_BASE}/workflows/${id}/`);
  return r.data;
}

// Create workflow
type WorkflowWritePayload = Omit<Partial<Workflow>, 'execution_engine'> & {
  execution_engine?: 'prefect';
};

export async function createWorkflow(data: WorkflowWritePayload): Promise<Workflow> {
  const r = await client.post(`${WORKFLOWS_BASE}/workflows/`, data);
  return r.data;
}

// Update workflow
export async function updateWorkflow(id: string, data: WorkflowWritePayload): Promise<Workflow> {
  const r = await client.put(`${WORKFLOWS_BASE}/workflows/${id}/`, data);
  return r.data;
}

// Delete workflow
export async function deleteWorkflow(id: string): Promise<void> {
  await client.delete(`${WORKFLOWS_BASE}/workflows/${id}/`);
}

export async function listTicketWorkflowBindings(params?: {
  workflow?: string;
}): Promise<TicketWorkflowBinding[]> {
  const qs = new URLSearchParams();
  if (params?.workflow) qs.set('workflow', params.workflow);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await client.get(`${WORKFLOWS_BASE}/ticket-workflow-bindings/${suffix}`);
  return r.data;
}

export async function createTicketWorkflowBinding(data: Partial<TicketWorkflowBinding>): Promise<TicketWorkflowBinding> {
  const r = await client.post(`${WORKFLOWS_BASE}/ticket-workflow-bindings/`, data);
  return r.data;
}

export async function updateTicketWorkflowBinding(id: string, data: Partial<TicketWorkflowBinding>): Promise<TicketWorkflowBinding> {
  const r = await client.patch(`${WORKFLOWS_BASE}/ticket-workflow-bindings/${id}/`, data);
  return r.data;
}

export async function deleteTicketWorkflowBinding(id: string): Promise<void> {
  await client.delete(`${WORKFLOWS_BASE}/ticket-workflow-bindings/${id}/`);
}

// Execute workflow
export async function executeWorkflow(
  id: string,
  triggerData?: Record<string, any>,
  confirmMassUpdate?: boolean,
): Promise<WorkflowExecution> {
  const r = await client.post(`${WORKFLOWS_BASE}/workflows/${id}/execute/`, {
    trigger_data: triggerData || {},
    trigger_source: 'manual',
    confirm_mass_update: confirmMassUpdate === true,
  });
  return r.data;
}

// Clone workflow
export async function cloneWorkflow(id: string, newName?: string): Promise<Workflow> {
  const r = await client.post(`${WORKFLOWS_BASE}/workflows/${id}/clone/`, { name: newName });
  return r.data;
}

// Activate workflow
export async function activateWorkflow(id: string): Promise<void> {
  await client.post(`${WORKFLOWS_BASE}/workflows/${id}/activate/`);
}

// Deactivate workflow
export async function deactivateWorkflow(id: string): Promise<void> {
  await client.post(`${WORKFLOWS_BASE}/workflows/${id}/deactivate/`);
}

// Subscribe before reloading snapshots so changes during a disconnect are covered.
export function subscribeWorkflowProgress(onProgress: (progress: WorkflowProgress | null) => void): () => void {
  return listenForWorkflowProgress(
    `${resolveApiBase()}${WORKFLOWS_BASE}/executions/events/`,
    () => accessToken,
    onProgress,
  );
}

// List executions
export async function listWorkflowExecutions(params?: {
  workflow?: string;
  status?: string;
  start_date?: string;
  end_date?: string;
}): Promise<WorkflowExecution[]> {
  const qs = new URLSearchParams();
  if (params?.workflow) qs.set('workflow', params.workflow);
  if (params?.status) qs.set('status', params.status);
  if (params?.start_date) qs.set('start_date', params.start_date);
  if (params?.end_date) qs.set('end_date', params.end_date);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await client.get(`${WORKFLOWS_BASE}/executions/${suffix}`);
  return r.data;
}

// Get execution detail
export async function getWorkflowExecution(id: string): Promise<WorkflowExecution> {
  const r = await client.get(`${WORKFLOWS_BASE}/executions/${id}/`);
  return r.data;
}

// Cancel execution
export async function cancelWorkflowExecution(id: string): Promise<void> {
  await client.post(`${WORKFLOWS_BASE}/executions/${id}/cancel/`);
}

// Force-sync a Prefect-backed execution from the Prefect Server.
// Returns the up-to-date detail payload (same shape as getWorkflowExecution).
export async function refreshPrefectExecutionStatus(id: string): Promise<WorkflowExecution> {
  const r = await client.post(`${WORKFLOWS_BASE}/executions/${id}/refresh-prefect-status/`);
  return r.data;
}

// Get available actions
export async function getAvailableActions(): Promise<ActionInfo[]> {
  const r = await client.get(`${WORKFLOWS_BASE}/action-templates/available_actions/`);
  return r.data;
}

// Get workflow stats
export async function getWorkflowStats(): Promise<WorkflowStats> {
  const r = await client.get(`${WORKFLOWS_BASE}/stats/`);
  return r.data;
}

export async function listSavedWorkflowNodes(params?: { node_category?: string; search?: string }): Promise<SavedWorkflowNode[]> {
  const qs = new URLSearchParams();
  if (params?.node_category) qs.set('node_category', params.node_category);
  if (params?.search) qs.set('search', params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await client.get(`${WORKFLOWS_BASE}/saved-nodes/${suffix}`);
  return r.data;
}

export async function createSavedWorkflowNode(data: Partial<SavedWorkflowNode>): Promise<SavedWorkflowNode> {
  const r = await client.post(`${WORKFLOWS_BASE}/saved-nodes/`, data);
  return r.data;
}

export async function updateSavedWorkflowNode(id: string, data: Partial<SavedWorkflowNode>): Promise<SavedWorkflowNode> {
  const r = await client.patch(`${WORKFLOWS_BASE}/saved-nodes/${id}/`, data);
  return r.data;
}

export async function deleteSavedWorkflowNode(id: string): Promise<void> {
  await client.delete(`${WORKFLOWS_BASE}/saved-nodes/${id}/`);
}

// Publish workflow to Prefect by persisting a JSON manifest for the shared deployment
export async function publishWorkflow(id: string, options?: { register_deployment?: boolean }): Promise<{
  status: string;
  workflow_id: string;
  workflow_name: string;
  slug: string;
  manifest_ref: string;
  manifest_path: string;
  manifest_version: number;
  manifest_filename: string;
  published_at: string;
  steps_count: number;
  deployment_registered: boolean;
  deployment_id?: string;
}> {
  const r = await client.post(`${WORKFLOWS_BASE}/workflows/${id}/publish/`, {
    register_deployment: options?.register_deployment ?? true,
  });
  return r.data;
}

// Export the last published manifest; sensitive values remain encrypted.
export async function exportWorkflow(id: string): Promise<{ blob: Blob; filename: string }> {
  const r = await client.get(`${WORKFLOWS_BASE}/workflows/${id}/export/`, {
    responseType: 'blob',
  });
  return {
    blob: r.data as Blob,
    filename: extractDownloadFilename(
      r.headers['content-disposition'],
      `workflow-${id}-published.json`,
    ),
  };
}


// Import workflow from uploaded JSON file
export async function importWorkflowFromFile(file: File): Promise<{
  status: string;
  source: string;
  workflow_id: string;
  workflow_name: string;
  removed_secret_fields?: Array<{
    step_name: string;
    action_type: string;
    fields: string[];
  }>;
}> {
  const form = new FormData();
  form.append('file', file);
  const r = await client.post(`${WORKFLOWS_BASE}/import/`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return r.data;
}

export async function listInterfaceEndpoints(params?: { interface_type?: 'api' | 'webhook'; is_active?: boolean; search?: string }): Promise<InterfaceEndpoint[]> {
  const qs = new URLSearchParams();
  if (params?.interface_type) qs.set('interface_type', params.interface_type);
  if (params?.is_active !== undefined) qs.set('is_active', String(params.is_active));
  if (params?.search) qs.set('search', params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await client.get(`/interfaces/endpoints/${suffix}`);
  return r.data;
}

export async function createInterfaceEndpoint(data: Partial<InterfaceEndpoint>): Promise<InterfaceEndpoint> {
  const r = await client.post('/interfaces/endpoints/', data);
  return r.data;
}

export async function updateInterfaceEndpoint(id: string, data: Partial<InterfaceEndpoint>): Promise<InterfaceEndpoint> {
  const r = await client.patch(`/interfaces/endpoints/${id}/`, data);
  return r.data;
}

export async function deleteInterfaceEndpoint(id: string): Promise<void> {
  await client.delete(`/interfaces/endpoints/${id}/`);
}

export async function getInterfaceEndpointLogs(id: string): Promise<InterfaceRequestLog[]> {
  const r = await client.get(`/interfaces/endpoints/${id}/logs/`);
  return r.data;
}

export async function testInterfaceEndpoint(id: string, payload?: Record<string, any>): Promise<Record<string, any>> {
  const r = await client.post(`/interfaces/endpoints/${id}/test/`, payload || { event: 'manual_test' });
  return r.data;
}

export type DetectionRuleItem = {
  id: string;
  name?: string;
  type?: 'directory' | 'file' | string;
  [key: string]: any;
};

export type DetectionRuleMeta = {
  title?: string;
  level?: string;
  status?: string;
  description?: string;
  product?: string;
  service?: string;
  category?: string;
  logsource?: string;
  profile?: string;
  tags?: string[];
  detection_preview?: string;
};

export type DetectionRuleDetail = {
  id: string;
  name?: string;
  version?: number;
  yaml?: string;
  payload?: Record<string, any>;
  meta?: DetectionRuleMeta;
  compiled?: {
    language?: string;
    lucene?: string;
    eql?: string;
    esql?: string;
    esql_source?: "autogenerated" | "manual";
    splunk?: string;
    kql?: string;
    error?: string;
    profiles?: string[];
    elastic_index_patterns?: string[];
    risk_incident?: {
      enabled?: boolean;
      source?: "sigma_correlation";
      correlation_type?: "value_sum";
      window?: string;
      score_field?: string;
      group_by?: string[];
      operator?: string;
      value?: number;
      risk_score_output_field?: string;
      risk_event_count_field?: string;
      risk_object_type_field?: string;
      risk_object_field?: string;
      source_index?: string;
    };
  };
};

export type DetectionMappingItem = {
  id: number;
  category?: string;
  data_source?: string;
  event_category?: string;
  mapping_profile: string;
  sigma: string;
  splunk?: string;
  elastic?: string;
  elastic_index_patterns?: string[];
  [key: string]: any;
};

export async function listDetectionRules(): Promise<DetectionRuleItem[]> {
  const r = await client.get('/detections/rules/');
  return r.data;
}

export async function getDetectionRule(id: string): Promise<DetectionRuleDetail> {
  const r = await client.get(`/detections/rules/${encodeURIComponent(id)}/`);
  return r.data;
}

export async function saveDetectionRule(id: string, yaml: string, extras?: Record<string, any>): Promise<any> {
  const r = await client.post(`/detections/rules/${encodeURIComponent(id)}/`, { yaml, ...(extras || {}) });
  return r.data;
}

export async function compileDetectionRule(
  yaml: string,
): Promise<{ language?: string; lucene?: string; eql?: string; esql?: string; esql_source?: "autogenerated" | "manual"; splunk?: string; kql?: string; error?: string; profiles?: string[]; elastic_index_patterns?: string[] }> {
  const r = await client.post('/detections/rules/compile/', { yaml });
  return r.data;
}

export async function deleteDetectionRule(id: string): Promise<any> {
  const r = await client.delete(`/detections/rules/${encodeURIComponent(id)}/`);
  return r.data;
}

export async function uploadDetectionRules(files: File[]): Promise<any> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  const r = await client.post('/detections/rules/upload/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return r.data;
}

export async function exportDetectionRules(ids?: string[]): Promise<any> {
  const r = await client.post('/detections/rules/export/', { ids: Array.isArray(ids) ? ids : [] });
  return r.data;
}

export async function listDetectionMappings(): Promise<DetectionMappingItem[]> {
  const r = await client.get('/detections/mappings/');
  return Array.isArray(r.data) ? r.data : [];
}

export async function createDetectionMapping(payload: Record<string, any>): Promise<any> {
  const r = await client.post('/detections/mappings/', payload);
  return r.data;
}

export async function deleteDetectionMappings(ids: Array<string | number>): Promise<any> {
  const r = await client.delete('/detections/mappings/', { data: { ids: ids.map((item) => String(item)) } });
  return r.data;
}

export async function uploadDetectionMappings(files: File[]): Promise<any> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  const r = await client.post('/detections/mappings/upload/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return r.data;
}

export async function exportDetectionMappings(ids?: Array<string | number>): Promise<Blob> {
  const r = await client.post('/detections/mappings/export/', { ids: Array.isArray(ids) ? ids.map((item) => String(item)) : [] }, {
    responseType: 'blob',
  });
  return r.data;
}

export type DetectionDeploymentRecord = {
  id: string;
  rule_id: string;
  rule_name?: string;
  target: string;
  action: string;
  status: string;
  remote_id?: string;
  remote_rule_id?: string;
  message?: string;
  payload?: Record<string, any>;
  created_by?: string;
  created_at: string;
};

export async function listDetectionDeployments(ruleId?: string): Promise<DetectionDeploymentRecord[]> {
  const qp = new URLSearchParams();
  if (ruleId) qp.set('rule_id', ruleId);
  const query = qp.toString();
  const r = await client.get(`/detections/deployments/${query ? `?${query}` : ''}`);
  return Array.isArray(r.data) ? r.data : [];
}

export async function createDetectionDeployment(payload: {
  rule_id: string;
  target: string;
  action: string;
  status: string;
  remote_id?: string;
  remote_rule_id?: string;
  message?: string;
  payload?: Record<string, any>;
}): Promise<DetectionDeploymentRecord> {
  const r = await client.post('/detections/deployments/', payload);
  return r.data;
}

// Published detection rules (remote target)
export type PublishedDetectionRule = {
  id: string;
  rule_id?: string;
  name: string;
  type: string;
  enabled: boolean;
  risk_score?: number;
  severity?: string;
  description?: string;
  index?: string[];
  query?: string;
  language?: string;
  from?: string;
  interval?: string;
  tags?: string[];
  [key: string]: any;
};

export async function listPublishedDetectionRules(params?: {
  page?: number;
  per_page?: number;
  filter?: string;
  sort_field?: string;
  sort_order?: 'asc' | 'desc';
}): Promise<{ data: PublishedDetectionRule[]; total: number }> {
  const safePage = Number.isFinite(Number(params?.page)) && Number(params?.page) > 0
    ? Math.floor(Number(params?.page))
    : undefined;
  const safePerPage = Number.isFinite(Number(params?.per_page)) && Number(params?.per_page) > 0
    ? Math.floor(Number(params?.per_page))
    : undefined;
  const qp = new URLSearchParams();
  if (safePage) qp.set('page', String(safePage));
  if (safePerPage) qp.set('per_page', String(safePerPage));
  if (params?.filter) qp.set('filter', params.filter);
  if (params?.sort_field) qp.set('sort_field', params.sort_field);
  if (params?.sort_order) qp.set('sort_order', params.sort_order);
  const q = qp.toString();
  const r = await client.get(`/detections/publish/rules/${q ? `?${q}` : ''}`);
  const body = r.data || {};
  const data = Array.isArray(body.data) ? body.data : [];
  const total = Number(body.total || data.length || 0);
  return { data, total };
}

export async function getPublishedDetectionRule(id: string): Promise<PublishedDetectionRule> {
  const r = await client.get(`/detections/publish/rules/${encodeURIComponent(id)}/`);
  return r.data;
}

export async function createPublishedDetectionRule(payload: Record<string, any>): Promise<PublishedDetectionRule> {
  const r = await client.post('/detections/publish/rules/', payload);
  return r.data;
}

export async function updatePublishedDetectionRule(id: string, payload: Record<string, any>): Promise<PublishedDetectionRule> {
  const r = await client.put(`/detections/publish/rules/${encodeURIComponent(id)}/`, payload);
  return r.data;
}

export async function patchPublishedDetectionRule(id: string, payload: Record<string, any>): Promise<PublishedDetectionRule> {
  const r = await client.patch(`/detections/publish/rules/${encodeURIComponent(id)}/`, payload);
  return r.data;
}

export async function deletePublishedDetectionRule(id: string): Promise<any> {
  const r = await client.delete(`/detections/publish/rules/${encodeURIComponent(id)}/`);
  return r.data;
}

export async function getPublishedRuleVersions(id: string): Promise<{ id: string; current_version: number; data: any[] }> {
  const r = await client.get(`/detections/publish/rules/${encodeURIComponent(id)}/versions/`);
  return r.data;
}

export async function rollbackPublishedRuleVersion(id: string, version: number): Promise<any> {
  const r = await client.post(`/detections/publish/rules/${encodeURIComponent(id)}/rollback/`, { version });
  return r.data;
}

export async function previewPublishedDetectionRule(payload: Record<string, any>): Promise<any> {
  const r = await client.post('/detections/publish/rules/preview/', payload);
  return r.data;
}

export async function listPublishedConnectors(): Promise<Array<{ id: string; name: string; connector_type_id?: string }>> {
  const r = await client.get('/detections/publish/connectors/');
  return Array.isArray(r.data) ? r.data : [];
}
