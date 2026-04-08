import React, { useEffect, useMemo, useState } from 'react';
import dayjs from 'dayjs';
import { Button, Card, Checkbox, Col, Descriptions, Divider, Empty, Form, Input, List, Modal, Row, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { SlaTicketAttachment, SlaTicketDetail, SlaTicketHandleLog, SlaTicketLabel, SlaTicketWorkLog } from 'types';
import { aiAssistantChat, clearSlaTicketAiChatHistory, fetchSlaTicketAiChatHistory, fetchSlaTicketHandleLogs, fetchSlaTicketFieldChoices, generateSlaTicketAiAssistant, generateSlaTicketAiMention, resolveSlaTicket, toggleSlaTicketPending, updateSlaTicket } from 'services/tickets';

type Props = {
  ticket: SlaTicketDetail;
  attachments: SlaTicketAttachment[];
  workLogs: SlaTicketWorkLog[];
  statusValue: string;
  notesValue: string;
  onStatusChange: (v: string) => void;
  onNotesChange: (v: string) => void;
  onSubmitStatus: (statusOverride?: string) => void;
  onAddWorkLog?: (logEntry: string) => void | Promise<void>;
  onUploadWorkLogImage?: (file: File) => Promise<SlaTicketAttachment>;
  onRefresh: () => void;
  loading?: boolean;
};

const statusLabel: Record<string, string> = {
  new: 'New',
  acknowledged: 'Acknowledged',
  triaged: 'Triaged',
  contained: 'Contained',
  resolved: 'Resolved',
  closed: 'Closed',
};

const renderStatusTag = (s?: string) => {
  const key = (s || 'unknown').toLowerCase();
  const cls = `sla-status-tag sla-status-${key}`;
  return <Tag className={cls}>{statusLabel[key] ?? s ?? 'unknown'}</Tag>;
};

  const renderSeverityTag = (p?: string, label?: string) => {
  const key = (p || 'unknown').toLowerCase();
  const cls = `sla-severity-tag sla-severity-${key}`;
  return <Tag className={cls}>{label ?? p ?? 'unknown'}</Tag>;
};

const maybe = (v: any) => (v === undefined || v === null || v === '' ? undefined : v);

const formatTimestamp = (value?: string | null) => {
  if (!value) return '';
  const dt = dayjs(value);
  return dt.isValid() ? dt.format('YYYY-MM-DD HH:mm:ss') : String(value);
};

const fixMojibake = (value: string) => {
  const text = String(value || '');
  if (!text) return text;
  const looksBroken = /[ÃÂâåäæçèéêëìíîïðñòóôöõøùúûüýÿ]/.test(text);
  if (!looksBroken) return text;
  try {
    const bytes = Uint8Array.from(text, (c) => c.charCodeAt(0) & 0xff);
    const decoded = new TextDecoder('utf-8', { fatal: false }).decode(bytes);
    const decodedLooksBetter = /[\u4e00-\u9fff]/.test(decoded) && decoded.length >= text.length * 0.6;
    return decodedLooksBetter ? decoded : text;
  } catch {
    return text;
  }
};

const formatTicketLabels = (labels: SlaTicketLabel[] | undefined) => {
  if (!Array.isArray(labels) || labels.length === 0) return [];
  return labels
    .map((label) => {
      if (!label || !label.label_name) return null;
      const name = String(label.label_name).trim();
      if (!name) return null;
      const value = label.label_value == null ? '' : String(label.label_value).trim();
      return `${name}:${value}`;
    })
    .filter(Boolean) as string[];
};

const renderLogEntry = (entry: string) => {
  const text = (entry || '').trim();
  if (!text) return null;

  const images: Array<{ alt: string; url: string }> = [];
  const mdImg = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let m: RegExpExecArray | null;
  while ((m = mdImg.exec(text))) {
    images.push({ alt: m[1] || 'image', url: m[2] });
  }
  const urlImgs: string[] = [];
  const urlRe = /(https?:\/\/[^\s)]+?\.(?:png|jpe?g|gif|webp))/gi;
  while ((m = urlRe.exec(text))) {
    urlImgs.push(m[1]);
  }
  for (const u of urlImgs) {
    if (!images.some((x) => x.url === u)) images.push({ alt: 'image', url: u });
  }

  const looksLikeCommand = /^(command:|action|result|parameters|outputs|status)/i.test(text);

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ whiteSpace: 'pre-wrap', fontFamily: looksLikeCommand ? 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' : undefined, fontSize: looksLikeCommand ? 12 : 13 }}>
        {text}
      </div>
      {images.length ? (
        <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
          {images.map((img, idx) => (
            <a key={`${img.url}_${idx}`} href={img.url} target="_blank" rel="noreferrer" style={{ display: 'block' }}>
              <img
                src={img.url}
                alt={img.alt}
                style={{ width: '100%', borderRadius: 8, border: '1px solid rgba(0,0,0,0.08)' }}
              />
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
};

function WarRoomView({
  ticket,
  workLogs,
  handleLogs,
  attachments,
  onAddWorkLog,
  onUploadWorkLogImage,
  loading,
}: {
  ticket: SlaTicketDetail;
  workLogs: SlaTicketWorkLog[];
  handleLogs: SlaTicketHandleLog[];
  attachments: SlaTicketAttachment[];
  onAddWorkLog?: (logEntry: string) => void | Promise<void>;
  onUploadWorkLogImage?: (file: File) => Promise<SlaTicketAttachment>;
  loading?: boolean;
}) {
  const [tab, setTab] = useState<string>('all');
  const [draft, setDraft] = useState<string>('');

  const allLogs = (workLogs || []).slice().reverse();
  const history = handleLogs || [];
  const statusChangeLogs = allLogs.filter((w) => /status changed/i.test(String(w?.log_entry || '')));
  const workLogItems = allLogs.filter((w) => !/status changed/i.test(String(w?.log_entry || '')));
  const files = attachments || [];
  const historyCount = history.length || statusChangeLogs.length;

  const send = async () => {
    if (!onAddWorkLog) return;
    const text = (draft || '').trim();
    if (!text) return;
    setDraft('');
    await onAddWorkLog(text);
  };

  const onPaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!onUploadWorkLogImage) return;
    const items = Array.from(e.clipboardData?.items || []);
    const img = items.find((it) => it.kind === 'file' && (it.type || '').startsWith('image/'));
    if (!img) return;
    const file = img.getAsFile();
    if (!file) return;
    e.preventDefault();
    try {
      const uploaded = await onUploadWorkLogImage(file);
      const url = uploaded?.file_path;
      if (url) {
        setDraft((prev) => {
          const prefix = prev && !prev.endsWith('\n') ? '\n' : '';
          return `${prev}${prefix}![${uploaded.file_name || 'image'}](${url})\n`;
        });
      }
    } catch {
      // swallow here; parent shows errors
    }
  };

  const renderHandleLogs = () => (
    <Card size="small" title={history.length ? 'Handle Logs' : 'Status Changes'} styles={{ body: { padding: 0 } }}>
      <div style={{ maxHeight: 520, overflow: 'auto', padding: 12 }}>
        {history.length ? (
          <List
            dataSource={history}
            renderItem={(h) => (
              <List.Item style={{ alignItems: 'flex-start' }}>
                <div style={{ width: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <div style={{ fontWeight: 600 }}>{h.handler_username || 'System'}</div>
                    <div style={{ color: 'rgba(0,0,0,0.45)', whiteSpace: 'nowrap' }}>{h.handled_at || ''}</div>
                  </div>
                  <div style={{ marginTop: 6, whiteSpace: 'pre-wrap', fontSize: 13 }}>
                    {h.action_taken}
                  </div>
                </div>
              </List.Item>
            )}
          />
        ) : statusChangeLogs.length ? (
          <List
            dataSource={statusChangeLogs}
            renderItem={(w) => (
              <List.Item style={{ alignItems: 'flex-start' }}>
                <div style={{ width: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <div style={{ fontWeight: 600 }}>{w.created_by_username || 'System'}</div>
                    <div style={{ color: 'rgba(0,0,0,0.45)', whiteSpace: 'nowrap' }}>{w.created_at}</div>
                  </div>
                  {renderLogEntry(w.log_entry)}
                </div>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="No history yet" />
        )}
      </div>
    </Card>
  );

  const renderWorkLogs = () => (
    <Card size="small" title="Work Logs" styles={{ body: { padding: 0 } }}>
      <div style={{ maxHeight: 420, overflow: 'auto', padding: 12 }}>
        {workLogItems.length ? (
          <List
            dataSource={workLogItems}
            renderItem={(w) => (
              <List.Item style={{ alignItems: 'flex-start' }}>
                <div style={{ width: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <div style={{ fontWeight: 600 }}>{w.created_by_username || 'System'}</div>
                    <div style={{ color: 'rgba(0,0,0,0.45)', whiteSpace: 'nowrap' }}>{w.created_at}</div>
                  </div>
                  {renderLogEntry(w.log_entry)}
                </div>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="No messages yet" />
        )}
      </div>

      <div style={{ borderTop: '1px solid #f0f0f0', padding: 12 }}>
        <Space style={{ width: '100%' }} direction="vertical" size={8}>
          <Input.TextArea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={onPaste}
            placeholder={onAddWorkLog ? 'Type a message...' : 'Posting disabled'}
            autoSize={{ minRows: 2, maxRows: 4 }}
            disabled={!onAddWorkLog || loading}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Work logs are backed by ticket work logs
            </Typography.Text>
            <Button type="primary" onClick={send} disabled={!onAddWorkLog || !draft.trim()} loading={loading}>
              Send
            </Button>
          </div>
        </Space>
      </div>
    </Card>
  );

  return (
    <div style={{ padding: 16, background: 'transparent', minHeight: 520 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Space wrap size={10}>
          <Typography.Text style={{ fontWeight: 700 }}>
            War Room
          </Typography.Text>
          <Tag color="geekblue">{ticket.ticket_number}</Tag>
          {renderStatusTag(ticket.status)}
        </Space>
        <Input.Search
          allowClear
          placeholder="Search (e.g. hostName)"
          style={{ width: 320 }}
          disabled
        />
      </div>

      <div style={{ marginTop: 12 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: 'all', label: `All (${workLogItems.length + historyCount})` },
            { key: 'work_logs', label: `Work Logs (${workLogItems.length})` },
            { key: 'handle_logs', label: `Handle Logs (${historyCount})` },
            { key: 'files', label: `Files (${files.length})` },
            { key: 'evidence', label: `Evidence (${files.length})` },
          ]}
          tabBarStyle={{ marginBottom: 10, color: 'rgba(255,255,255,0.72)' }}
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {(tab === 'files' || tab === 'evidence') ? (
          <Card size="small" title={tab === 'files' ? `Files (${files.length})` : `Evidence (${files.length})`}>
            {files.length ? (
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                columns={[
                  { title: 'File', dataIndex: 'file_name', key: 'file_name', ellipsis: true },
                  { title: 'Uploaded', dataIndex: 'uploaded_time', key: 'uploaded_time', width: 200 },
                  {
                    title: 'Action',
                    key: 'action',
                    width: 120,
                    render: (_: any, r: SlaTicketAttachment) => (
                      <Button size="small" href={r.file_path} target="_blank" rel="noreferrer">
                        Download
                      </Button>
                    ),
                  },
                ]}
                dataSource={files}
              />
            ) : (
              <Empty description="No files yet" />
            )}
          </Card>
        ) : tab === 'handle_logs' ? (
          renderHandleLogs()
        ) : tab === 'all' ? (
          <>
            {renderHandleLogs()}
            {renderWorkLogs()}
          </>
        ) : (
          renderWorkLogs()
        )}
      </div>
    </div>
  );
}

export default function SlaTicketDetailView(props: Props) {
  const { ticket, attachments, workLogs, statusValue, notesValue, onStatusChange, onNotesChange, onSubmitStatus, onRefresh, loading } = props;
  const [showEmpty, setShowEmpty] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('incident');
  const [incidentTab, setIncidentTab] = useState<string>('timeline');
  const [isPending, setIsPending] = useState(false);
  const [pendingLoading, setPendingLoading] = useState(false);
  const [handleLogs, setHandleLogs] = useState<SlaTicketHandleLog[]>([]);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResult, setAiResult] = useState<any | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string; hidden?: boolean; trace?: any[] }>>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatHistoryLoading, setChatHistoryLoading] = useState(false);
  const [chatMoreLoading, setChatMoreLoading] = useState(false);
  const [chatNextBefore, setChatNextBefore] = useState<string | null>(null);
  const [collapsedTraces, setCollapsedTraces] = useState<Record<number, boolean>>({});
  const [expandedTaskIndex, setExpandedTaskIndex] = useState<number | null>(null);
  const [expandedNextTaskIndex, setExpandedNextTaskIndex] = useState<number | null>(null);
  const [expandedTimelineIndex, setExpandedTimelineIndex] = useState<number | null>(null);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [resolveLoading, setResolveLoading] = useState(false);
  const [resolveSubmitting, setResolveSubmitting] = useState(false);
  const [resolveChoices, setResolveChoices] = useState<any | null>(null);
  const [resolveForm] = Form.useForm();
  const [commentDraft, setCommentDraft] = useState('');
  const [commentSending, setCommentSending] = useState(false);
  const [editableLabels, setEditableLabels] = useState<Array<{ label_name: string; label_value: string }>>([]);
  const [newLabelName, setNewLabelName] = useState('');
  const [newLabelValue, setNewLabelValue] = useState('');
  const [labelsSaving, setLabelsSaving] = useState(false);

  const notesCount = workLogs?.length ?? 0;

  useEffect(() => {
    try {
      const persisted = localStorage.getItem(`siem_sla_pending_${ticket.ticket_number}`);
      setIsPending(persisted === '1');
    } catch {
      setIsPending(false);
    }
  }, [ticket.ticket_number]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetchSlaTicketHandleLogs(ticket.ticket_number);
        const parsed = Array.isArray(res) ? res : [];
        if (alive) setHandleLogs(parsed);
      } catch {
        if (alive) setHandleLogs([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, [ticket.ticket_number]);

  useEffect(() => {
    const parsed = (ticket.labels || []).map((label) => ({
      label_name: String(label?.label_name || ''),
      label_value: label?.label_value == null ? '' : String(label.label_value),
    }));
    setEditableLabels(parsed);
  }, [ticket.ticket_number, ticket.labels]);

  useEffect(() => {
    setChatOpen(false);
    setChatMessages([]);
    setChatInput('');
    setCollapsedTraces({});
    setChatNextBefore(null);
  }, [ticket.ticket_number]);

  const buildAiBasePayload = () => {
    const aiEnabled = localStorage.getItem('siem_ai_enabled') !== '0';
    const aiApiKey = localStorage.getItem('siem_ai_api_key') || '';
    const aiModel = localStorage.getItem('siem_ai_model') || '';
    const aiBaseUrl = localStorage.getItem('siem_ai_base_url') || '';
    const aiTimeoutRaw = localStorage.getItem('siem_ai_timeout') || '';
    const aiTimeout = aiTimeoutRaw && !Number.isNaN(Number(aiTimeoutRaw)) ? Number(aiTimeoutRaw) : undefined;

    return {
      enabled: aiEnabled,
      api_key: aiApiKey || undefined,
      model: aiModel || undefined,
      base_url: aiBaseUrl || undefined,
      timeout_seconds: aiTimeout,
    };
  };

  const buildChatContext = () => {
    const context = {
      ticket_number: ticket.ticket_number,
      title: ticket.title,
      status: ticket.status,
      priority: ticket.priority,
      event_category: ticket.event_category,
      event_result: ticket.event_result,
      event_platform: ticket.event_platform,
      event_sources: ticket.event_sources,
      alert_message: ticket.alert_message,
    };
    return `Ticket context (read-only):\n${JSON.stringify(context, null, 2)}`;
  };

  const loadChatHistory = async () => {
    setChatHistoryLoading(true);
    const system = { role: 'system', content: buildChatContext(), hidden: true };
    try {
      const res = await fetchSlaTicketAiChatHistory(ticket.ticket_number, { limit: 50 });
      const rows = Array.isArray(res?.messages) ? res.messages : Array.isArray(res) ? res : [];
      const normalized = rows
        .filter((row: any) => row && row.role && row.content != null)
        .map((row: any) => ({
          role: String(row.role),
          content: fixMojibake(String(row.content)),
          trace: Array.isArray(row.trace) ? row.trace : [],
        }));
      setChatNextBefore(res?.next_before || null);
      setChatMessages([system, ...normalized]);
    } catch {
      setChatMessages([system]);
      setChatNextBefore(null);
    } finally {
      setChatHistoryLoading(false);
    }
  };

  const loadMoreChatHistory = async () => {
    if (!chatNextBefore || chatMoreLoading) return;
    setChatMoreLoading(true);
    try {
      const res = await fetchSlaTicketAiChatHistory(ticket.ticket_number, { limit: 50, before: chatNextBefore });
      const rows = Array.isArray(res?.messages) ? res.messages : [];
      const normalized = rows
        .filter((row: any) => row && row.role && row.content != null)
        .map((row: any) => ({
          role: String(row.role),
          content: fixMojibake(String(row.content)),
          trace: Array.isArray(row.trace) ? row.trace : [],
        }));
      setChatNextBefore(res?.next_before || null);
      setChatMessages((prev) => {
        const system = prev.find((m) => m.hidden) || { role: 'system', content: buildChatContext(), hidden: true };
        const rest = prev.filter((m) => !m.hidden);
        return [system, ...normalized, ...rest];
      });
    } catch {
      setChatNextBefore(null);
    } finally {
      setChatMoreLoading(false);
    }
  };

  const runAiAssistant = async () => {
    setAiLoading(true);
    setAiError(null);
    try {
      const payload = {
        ...buildAiBasePayload(),
        alert_json: ticket.alert_message ? { raw: ticket.alert_message } : undefined,
        trigger_rule: ticket.event_category || '',
        related_logs: (workLogs || []).slice(0, 5).map((w) => w.log_entry),
      };
      const res = await generateSlaTicketAiAssistant(ticket.ticket_number, payload);
      setAiResult(res?.assistant || res);
      message.success('AI assistant updated');
    } catch (err: any) {
      const apiError = err?.response?.data?.error || err?.response?.data?.detail;
      setAiError(apiError ? String(apiError) : 'AI assistant failed');
      message.error(apiError ? String(apiError) : 'AI assistant failed');
    } finally {
      setAiLoading(false);
    }
  };

  const openChat = () => {
    setChatOpen(true);
    if (!chatMessages.length) {
      loadChatHistory();
    }
  };

  const sendChat = async () => {
    const text = chatInput.trim();
    if (!text || chatLoading) return;
    const historyForApi = chatMessages.map((m) => ({ role: m.role, content: m.content }));
    setChatMessages((prev) => [...prev, { role: 'user', content: text }]);
    setChatInput('');
    setChatLoading(true);
    try {
      const payload = {
        ...buildAiBasePayload(),
        message: text,
        messages: historyForApi,
        ticket_number: ticket.ticket_number,
      };
      const res = await aiAssistantChat(payload);
      const reply = fixMojibake(String(res?.response || ''));
      const trace = Array.isArray(res?.trace) ? res.trace : [];
      setChatMessages((prev) => {
        const next = [...prev, { role: 'assistant', content: reply || 'No response', trace }];
        const index = next.length - 1;
        setCollapsedTraces((state) => ({ ...state, [index]: true }));
        return next;
      });
    } catch (err: any) {
      const apiError = err?.response?.data?.error || err?.response?.data?.detail || 'Chat failed';
      setChatMessages((prev) => {
        const next = [...prev, { role: 'assistant', content: fixMojibake(String(apiError)), trace: [] }];
        const index = next.length - 1;
        setCollapsedTraces((state) => ({ ...state, [index]: true }));
        return next;
      });
    } finally {
      setChatLoading(false);
    }
  };

  const askAiByMention = async (prompt: string) => {
    setAiLoading(true);
    setAiError(null);
    try {
      if (!props.onAddWorkLog) {
        message.warning('Worklog is unavailable');
        return;
      }
      const payload = {
        ...buildAiBasePayload(),
        prompt,
        alert_json: ticket.alert_message ? { raw: ticket.alert_message } : undefined,
        related_logs: [],
      };
      const res = await generateSlaTicketAiMention(ticket.ticket_number, payload);
      const observablesPayload = res?.observables;
      if (observablesPayload && typeof observablesPayload === 'object') {
        try {
          await props.onAddWorkLog(`AI Observables JSON: ${JSON.stringify(observablesPayload)}`);
        } catch {}
      }
      const assistantRaw = typeof res?.assistant_raw === 'string' ? res.assistant_raw.trim() : '';
      const assistantObj = res?.assistant;
      let reply = assistantRaw;
      if (!reply && typeof assistantObj === 'string') {
        reply = assistantObj.trim();
      }
      if (!reply && assistantObj && typeof assistantObj === 'object') {
        if (typeof assistantObj.alert_explanation === 'string' && assistantObj.alert_explanation.trim()) {
          reply = assistantObj.alert_explanation.trim();
        } else {
          try {
            reply = JSON.stringify(assistantObj, null, 2);
          } catch {
            reply = String(assistantObj);
          }
        }
      }
      if (!reply) {
        try {
          reply = JSON.stringify(res, null, 2);
        } catch {
          reply = 'No AI reply';
        }
      }
      await props.onAddWorkLog(`AI Reply (@ai):\n${reply}`);
      setAiResult(null);
      message.success('AI replied');
    } catch (err: any) {
      const apiError = err?.response?.data?.error || err?.response?.data?.detail;
      setAiError(apiError ? String(apiError) : 'AI request failed');
      message.error(apiError ? String(apiError) : 'AI request failed');
    } finally {
      setAiLoading(false);
    }
  };

  const sendComment = async () => {
    const text = (commentDraft || '').trim();
    if (!text) return;
    setCommentSending(true);
    try {
      if (text.toLowerCase().startsWith('@ai')) {
        const prompt = text.replace(/^@ai\s*/i, '').trim();
        if (!prompt) {
          message.warning('Please add prompt after @ai');
          return;
        }
        await askAiByMention(prompt);
      } else if (props.onAddWorkLog) {
        await props.onAddWorkLog(text);
      } else {
        message.warning('Posting is unavailable');
        return;
      }
      setCommentDraft('');
    } finally {
      setCommentSending(false);
    }
  };

  const updateDecisionStatus = (nextStatus: string) => {
    onStatusChange(nextStatus);
    onSubmitStatus(nextStatus);
  };

  const canAcknowledge = ticket.status === 'new';
  const canTriaged = ticket.status === 'acknowledged';
  const canContained = ticket.status === 'triaged';
  const canResolved = ticket.status === 'triaged' || ticket.status === 'contained';

  useEffect(() => {
    if (!resolveOpen) return;
    let alive = true;
    setResolveLoading(true);
    (async () => {
      try {
        const res = await fetchSlaTicketFieldChoices();
        if (alive) setResolveChoices(res || null);
      } catch {
        if (alive) setResolveChoices(null);
      } finally {
        if (alive) setResolveLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [resolveOpen]);

  const submitResolve = async () => {
    if (!ticket.ticket_number) return;
    setResolveSubmitting(true);
    try {
      const values = await resolveForm.validateFields();
      await resolveSlaTicket(ticket.ticket_number, {
        event_category: values.event_category || undefined,
        event_result: values.event_result || undefined,
        notes: values.notes || undefined,
      });
      message.success('Resolved');
      setResolveOpen(false);
      resolveForm.resetFields();
      onStatusChange('resolved');
      onRefresh();
    } catch (err: any) {
      if (err?.errorFields) return;
      const apiError = err?.response?.data?.error || err?.response?.data?.detail;
      message.error(apiError ? String(apiError) : 'Resolve failed');
    } finally {
      setResolveSubmitting(false);
    }
  };

  const canTogglePending = ticket.status !== 'closed' && ticket.status !== 'new';
  const togglePending = async () => {
    if (ticket.status === 'closed') {
      message.error('Closed tickets cannot be set to pending');
      return;
    }
    if (ticket.status === 'new') {
      message.error('Pending is only available after the ticket is acknowledged');
      return;
    }
    setPendingLoading(true);
    try {
      await toggleSlaTicketPending(ticket.ticket_number);
      setIsPending((prev) => {
        const next = !prev;
        try { localStorage.setItem(`siem_sla_pending_${ticket.ticket_number}`, next ? '1' : '0'); } catch {}
        return next;
      });
      message.success('Pending state updated');
      onRefresh();
    } catch (err: any) {
      const apiError = err?.response?.data?.error || err?.response?.data?.detail;
      message.error(apiError ? String(apiError) : 'Failed to toggle pending');
    } finally {
      setPendingLoading(false);
    }
  };

  const attachmentColumns: ColumnsType<SlaTicketAttachment> = useMemo(() => ([
    { title: 'File', dataIndex: 'file_name', key: 'file_name', ellipsis: true },
    { title: 'Uploaded', dataIndex: 'uploaded_time', key: 'uploaded_time', width: 180 },
    {
      title: 'Action',
      key: 'action',
      width: 120,
      render: (_: any, r) => (
        <Button size="small" href={r.file_path} target="_blank" rel="noreferrer">
          Download
        </Button>
      ),
    },
  ]), []);

  const observableEvidenceItems = useMemo(() => {
    const marker = 'AI Observables JSON:';
    const rows: Array<{ key: string; type: string; value: string; source: string; created_at: string }> = [];
    const dedupe = new Set<string>();

    for (const w of workLogs || []) {
      const text = String(w?.log_entry || '');
      if (!text.includes(marker)) continue;
      const line = text.split(/\r?\n/).find((l) => l.includes(marker));
      if (!line) continue;
      const payload = line.slice(line.indexOf(marker) + marker.length).trim();
      if (!payload) continue;

      let parsed: any = null;
      try {
        parsed = JSON.parse(payload);
      } catch {
        parsed = null;
      }
      if (!parsed || typeof parsed !== 'object') continue;

      const pushRow = (typ: string, val: any, source = 'observables-mcp') => {
        const type = String(typ || '').trim();
        const value = String(val || '').trim();
        if (!type || !value) return;
        const dk = `${type}::${value}`;
        if (dedupe.has(dk)) return;
        dedupe.add(dk);
        rows.push({
          key: `${dk}::${String(w?.created_at || '')}`,
          type,
          value,
          source,
          created_at: String(w?.created_at || ''),
        });
      };

      if (Array.isArray(parsed?.items)) {
        for (const item of parsed.items) {
          if (!item || typeof item !== 'object') continue;
          pushRow(item.type, item.value);
        }
      }
      const obs = parsed?.observables;
      if (obs && typeof obs === 'object') {
        for (const [k, vals] of Object.entries(obs)) {
          if (!Array.isArray(vals)) continue;
          for (const v of vals) pushRow(k, v);
        }
      }
    }

    return rows;
  }, [workLogs]);

  const observableColumns: ColumnsType<any> = useMemo(() => ([
    { title: 'Type', dataIndex: 'type', key: 'type', width: 120 },
    { title: 'Value', dataIndex: 'value', key: 'value', ellipsis: true },
    { title: 'Source', dataIndex: 'source', key: 'source', width: 150 },
    { title: 'Observed At', dataIndex: 'created_at', key: 'created_at', width: 180 },
  ]), []);

  const indicators = useMemo(() => {
    const items: Array<{ key: string; value: any }> = [];
    if (ticket.event_sources !== undefined) items.push({ key: 'event_sources', value: ticket.event_sources });
    if (ticket.ticket_records !== undefined) items.push({ key: 'ticket_records', value: ticket.ticket_records });
    if (ticket.event_platform !== undefined) items.push({ key: 'event_platform', value: ticket.event_platform });
    return items;
  }, [ticket.event_sources, ticket.ticket_records, ticket.event_platform]);

  const incidentTitle = ticket.title || 'Incident';
  const ownerName = ticket.current_assign_owner || ticket.assigned_user_username || 'Unassigned';
  const riskScore = ticket.event_risk_score ?? '-';
  const summaryText = ticket.description
    ? ticket.description
    : [
        ticket.event_category ? `Category: ${ticket.event_category}` : '',
        ticket.event_result ? `Result: ${ticket.event_result}` : '',
      ].filter(Boolean).join(' | ') || 'No summary available yet.';

  const timelineItems = useMemo(() => {
    const base = (workLogs || []).slice();
    const tail = base.length > 6 ? base.slice(base.length - 6) : base;
    return tail.map((w) => ({
      time: w.created_at,
      text: w.log_entry,
    }));
  }, [workLogs]);

  const evidenceItems = useMemo(() => {
    const base = (attachments || []).slice();
    return base.length > 3 ? base.slice(0, 3) : base;
  }, [attachments]);

  const evidenceCount = (attachments?.length || 0) + observableEvidenceItems.length;

  const aiHeader = useMemo(() => {
    if (aiResult?.header) return aiResult.header;
    const logs = (workLogs || []).slice().reverse();
    for (const w of logs) {
      const text = String(w?.log_entry || '');
      const marker = 'AI Header JSON:';
      if (!text.includes(marker)) continue;
      const line = text.split(/\r?\n/).find((l) => l.includes(marker));
      if (!line) continue;
      const jsonText = line.slice(line.indexOf(marker) + marker.length).trim();
      try {
        return JSON.parse(jsonText);
      } catch {
        return null;
      }
    }
    return null;
  }, [aiResult, workLogs]);
  const aiTasks = useMemo(() => {
    if (Array.isArray(aiResult?.completed_tasks)) {
      return aiResult.completed_tasks;
    }
    const logs = (workLogs || []).slice().reverse();
    for (const w of logs) {
      const text = String(w?.log_entry || '');
      if (!text.includes('AI Assistant Result') || !text.includes('AI Tasks:')) continue;
      const idx = text.indexOf('AI Tasks:');
      if (idx < 0) continue;
      const nextIdx = text.indexOf('Next Tasks:', idx + 'AI Tasks:'.length);
      const tail = (nextIdx >= 0 ? text.slice(idx + 'AI Tasks:'.length, nextIdx) : text.slice(idx + 'AI Tasks:'.length)).trim();
      const lines = tail.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      const tasks = lines.map((l) => {
        const cleaned = l.replace(/^-+\s*/, '').trim();
        if (!cleaned) return null;
        const parts = cleaned.split(':');
        if (parts.length >= 2) {
          const title = parts.shift()?.trim() || '';
          const detail = parts.join(':').trim();
          return { title: title || detail, detail };
        }
        return { title: cleaned, detail: '' };
      }).filter(Boolean);
      if (tasks.length) return tasks;
    }
    return [];
  }, [aiResult, workLogs]);

  const nextTasks = useMemo(() => {
    if (Array.isArray(aiResult?.next_tasks)) {
      return aiResult.next_tasks;
    }
    const logs = (workLogs || []).slice().reverse();
    for (const w of logs) {
      const text = String(w?.log_entry || '');
      if (!text.includes('AI Assistant Result') || !text.includes('Next Tasks:')) continue;
      const idx = text.indexOf('Next Tasks:');
      if (idx < 0) continue;
      const tail = text.slice(idx + 'Next Tasks:'.length).trim();
      const lines = tail.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      const tasks = lines.map((l) => {
        const cleaned = l.replace(/^-+\s*/, '').trim();
        if (!cleaned) return null;
        const parts = cleaned.split(':');
        if (parts.length >= 2) {
          const title = parts.shift()?.trim() || '';
          const detail = parts.join(':').trim();
          return { title: title || detail, detail };
        }
        return { title: cleaned, detail: '' };
      }).filter(Boolean);
      if (tasks.length) return tasks;
    }
    return [];
  }, [aiResult, workLogs]);

  const caseDetailsItems = useMemo(() => {
    const rows: Array<{ label: string; value: any }> = [
      { label: 'Ticket Number', value: ticket.ticket_number },
      { label: 'SIEM Event ID', value: ticket.event_siem_id },
      { label: 'Status', value: renderStatusTag(ticket.status) },
      { label: 'Priority', value: renderSeverityTag(ticket.priority) },
      { label: 'Created', value: formatTimestamp(ticket.created_time) },
      { label: 'Updated', value: formatTimestamp(ticket.updated_time) },
    ];
    return rows.filter(r => showEmpty || maybe(r.value) !== undefined);
  }, [showEmpty, ticket]);

  const labelItems = useMemo(() => formatTicketLabels(editableLabels), [editableLabels]);

  const saveLabels = async (
    nextLabels: Array<{ label_name: string; label_value: string }>,
    successMessage: string,
  ) => {
    if (!ticket.ticket_number) return;

    const normalized: Array<{ label_name: string; label_value: string }> = [];
    const seen = new Set<string>();
    for (let i = 0; i < nextLabels.length; i++) {
      const row = nextLabels[i];
      const name = String(row?.label_name || '').trim();
      const value = String(row?.label_value || '').trim();

      if (!name) {
        message.warning(`Label #${i + 1} requires a label name`);
        return;
      }
      const key = `${name}::${value}`;
      if (seen.has(key)) {
        message.warning(`Duplicate label: ${name}:${value}`);
        return;
      }
      seen.add(key);
      normalized.push({ label_name: name, label_value: value });
    }

    setLabelsSaving(true);
    try {
      await updateSlaTicket(ticket.ticket_number, { labels: normalized });
      setEditableLabels(normalized);
      message.success(successMessage);
    } catch (err: any) {
      const apiError = err?.response?.data?.labels || err?.response?.data?.detail || err?.response?.data?.error;
      message.error(apiError ? String(apiError) : 'Failed to update labels');
    } finally {
      setLabelsSaving(false);
    }
  };

  const addLabel = async () => {
    const name = newLabelName.trim();
    const value = newLabelValue.trim();
    if (!name) {
      message.warning('Label name is required');
      return;
    }
    const next = [...editableLabels, { label_name: name, label_value: value }];
    await saveLabels(next, 'Label added');
    setNewLabelName('');
    setNewLabelValue('');
  };

  const deleteLabel = async (index: number) => {
    const next = editableLabels.filter((_, i) => i !== index);
    await saveLabels(next, 'Label deleted');
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <Space size={12} wrap>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {ticket.ticket_number}
          </Typography.Title>
          {renderStatusTag(ticket.status)}
          {renderSeverityTag(ticket.priority)}
        </Space>
        <Space>
          <Button onClick={togglePending} loading={pendingLoading} disabled={!canTogglePending}>
            {isPending ? 'In Progress' : 'Pending'}
          </Button>
          <Button onClick={onRefresh} loading={loading}>Refresh</Button>
          <Button>Search</Button>
          <Button>Settings</Button>
        </Space>
      </div>

      <div style={{ marginTop: 10 }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'incident', label: 'Incident Info' },
            { key: 'war_room', label: 'War Room' },
            { key: 'work_plan', label: 'Work Plan (1)' },
            { key: 'evidence', label: `Evidence (${evidenceCount})` },
            { key: 'related', label: 'Related Incidents' },
          ]}
        />
      </div>

      <div>
        {activeTab === 'war_room' ? (
          <WarRoomView
            ticket={ticket}
            workLogs={workLogs}
            handleLogs={handleLogs}
            attachments={attachments}
            onAddWorkLog={isPending ? undefined : props.onAddWorkLog}
            onUploadWorkLogImage={isPending ? undefined : props.onUploadWorkLogImage}
            loading={loading}
          />
        ) : activeTab !== 'incident' ? (
          <div style={{ paddingTop: 8 }}>
            {activeTab === 'work_plan' && (
              <Card size="small" title="Work Plan (1)">
                <Empty description="No work plan items yet" />
              </Card>
            )}
            {activeTab === 'evidence' && (
              <Card size="small" title={`Evidence (${evidenceCount})`}>
                <div style={{ display: 'grid', gap: 12 }}>
                  <div>
                    <Typography.Text strong>Observable Evidence</Typography.Text>
                    {observableEvidenceItems.length ? (
                      <Table
                        rowKey="key"
                        size="small"
                        pagination={{ pageSize: 6 }}
                        columns={observableColumns}
                        dataSource={observableEvidenceItems}
                        style={{ marginTop: 8 }}
                      />
                    ) : (
                      <div style={{ marginTop: 8 }}>
                        <Empty description="No observable evidence yet" />
                      </div>
                    )}
                  </div>
                  <div>
                    <Typography.Text strong>File Evidence</Typography.Text>
                    {attachments.length ? (
                      <Table
                        rowKey="id"
                        size="small"
                        pagination={false}
                        columns={attachmentColumns}
                        dataSource={attachments}
                        style={{ marginTop: 8 }}
                      />
                    ) : (
                      <div style={{ marginTop: 8 }}>
                        <Empty description="No file evidence yet" />
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            )}
            {activeTab === 'related' && <Empty description="No related incidents yet" />}
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr 320px', gap: 14 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Card size="small" title="Case Details">
                <Descriptions size="small" column={1} bordered>
                  {caseDetailsItems.map((r) => (
                    <Descriptions.Item key={r.label} label={r.label}>
                      {maybe(r.value) ?? <span style={{ color: 'rgba(0,0,0,0.35)' }}>-</span>}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
              <Card size="small" title="Decision Actions">
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <Button onClick={() => updateDecisionStatus('acknowledged')} disabled={!canAcknowledge}>Acknowledge</Button>
                  <Button type="primary" onClick={() => updateDecisionStatus('triaged')} disabled={!canTriaged}>Triaged</Button>
                  <Button onClick={() => updateDecisionStatus('contained')} disabled={!canContained}>Contained</Button>
                  <Button onClick={() => setResolveOpen(true)} disabled={!canResolved}>Resolved</Button>
                </div>
              </Card>
              <Card size="small" title="Resolution Summary">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="Ticket Category">
                    {ticket.event_category || <span style={{ color: 'rgba(0,0,0,0.35)' }}>-</span>}
                  </Descriptions.Item>
                  <Descriptions.Item label="Ticket Verdict">
                    {ticket.event_result || <span style={{ color: 'rgba(0,0,0,0.35)' }}>-</span>}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
              <Card size="small" title="Labels">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Space.Compact block>
                    <Input
                      placeholder="Label name"
                      value={newLabelName}
                      onChange={(e) => setNewLabelName(e.target.value)}
                      disabled={labelsSaving || loading}
                    />
                    <Input
                      placeholder="Label value (optional)"
                      value={newLabelValue}
                      onChange={(e) => setNewLabelValue(e.target.value)}
                      disabled={labelsSaving || loading}
                    />
                    <Button type="primary" onClick={addLabel} loading={labelsSaving || loading}>Add</Button>
                  </Space.Compact>

                  {labelItems.length ? (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {labelItems.map((item, idx) => (
                        <Tag
                          key={`${item}_${idx}`}
                          color="processing"
                          closable
                          onClose={(e) => {
                            e.preventDefault();
                            void deleteLabel(idx);
                          }}
                        >
                          {item}
                        </Tag>
                      ))}
                    </div>
                  ) : (
                    <span style={{ color: 'rgba(0,0,0,0.35)' }}>No labels</span>
                  )}
                </div>
              </Card>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Card>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{incidentTitle}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: '#666' }}>Score</span>
                  <Tag color="gold">{String(aiHeader?.score ?? riskScore)}</Tag>
                </div>
              </div>
              <div style={{ marginTop: 10, padding: 12, borderRadius: 8, background: '#fff7e6', border: '1px solid #ffd591' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 2c1.6 3.6 5 6.4 5 10.2 0 3.3-2.7 5.8-6 5.8s-6-2.5-6-5.8C5 8.3 8.5 5.3 12 2z" fill="#fa541c" />
                        <path d="M10 10c.9 1.4 2.8 2.6 2.8 4.3 0 1.4-1.2 2.5-2.7 2.5S7.4 15.7 7.4 14.3C7.4 12.7 9 11.5 10 10z" fill="#ffd591" />
                      </svg>
                      {renderSeverityTag(
                        aiHeader?.risk_level || ticket.priority,
                        aiHeader?.risk_level || ticket.priority || 'RISK',
                      )}
                    </span>
                    <span style={{ color: 'rgba(0,0,0,0.65)' }}>AI Confidence:</span>
                    <Tag color="gold">{aiHeader?.ai_confidence || '-'}</Tag>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ color: 'var(--access-secondary-text)' }}>Score</span>
                    <Tag color="gold">{String(aiHeader?.score ?? riskScore)}</Tag>
                  </div>
                </div>
                {aiHeader?.summary_title ? (
                  <div style={{ marginTop: 6, color: 'var(--text-primary)', whiteSpace: 'pre-wrap', fontSize: 15 }}>
                    {aiHeader.summary_title}
                  </div>
                ) : null}
              </div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 8, color: 'var(--text-primary)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>Status:</span>
                  {renderStatusTag(aiHeader?.status || ticket.status)}
                </div>
                <div>Owner: {ownerName}</div>
                <div>Platform: {aiHeader?.platform || ticket.event_platform || '-'}</div>
                <div>Source: {aiHeader?.source || ticket.event_sources || '-'}</div>
              </div>
            </Card>

              <Card size="small" title="AI Summary">
                <div style={{ whiteSpace: 'pre-wrap' }}>{summaryText}</div>
              </Card>

              <Card size="small">
                <Tabs
                  items={[
                    { key: 'timeline', label: 'Timeline' },
                    { key: 'alerts', label: 'Alerts' },
                    { key: 'response', label: 'Raw Message' },
                    { key: 'tasks', label: 'Tasks' },
                  ]}
                  activeKey={incidentTab}
                  onChange={setIncidentTab}
                />
                {incidentTab === 'timeline' ? (
                  <div style={{ maxHeight: 320, overflowY: 'auto', paddingRight: 4 }}>
                    {timelineItems.length ? (
                      <List
                        dataSource={timelineItems}
                        renderItem={(item, idx) => {
                          const text = String(item.text || '');
                          const short = text.length > 80 ? `${text.slice(0, 80)}...` : text;
                          const isOpen = expandedTimelineIndex === idx;
                          return (
                            <List.Item>
                              <div style={{ display: 'flex', gap: 12, width: '100%' }}>
                                <Tag color="gold">{item.time}</Tag>
                                <div style={{ flex: 1 }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                                    <div style={{ whiteSpace: 'pre-wrap' }}>{short}</div>
                                    {text.length > short.length ? (
                                      <Button size="small" type="link" onClick={() => setExpandedTimelineIndex(isOpen ? null : idx)}>
                                        {isOpen ? 'Hide' : 'Details'}
                                      </Button>
                                    ) : null}
                                  </div>
                                  {isOpen ? (
                                    <div style={{ marginTop: 6, whiteSpace: 'pre-wrap', color: 'var(--text-primary)' }}>
                                      {text}
                                    </div>
                                  ) : null}
                                </div>
                              </div>
                            </List.Item>
                          );
                        }}
                      />
                    ) : (
                      <Empty description="No timeline yet" />
                    )}
                  </div>
                ) : incidentTab === 'response' ? (
                  ticket.alert_message ? (
                    (() => {
                      const raw = String(ticket.alert_message || '');
                      let parsed: any = null;
                      try {
                        parsed = JSON.parse(raw);
                      } catch {
                        parsed = null;
                      }
                      if (parsed) {
                        const pretty = JSON.stringify(parsed, null, 2);
                        return (
                          <details open>
                            <summary style={{ cursor: 'pointer', color: 'rgba(0,0,0,0.65)' }}>JSON</summary>
                            <pre style={{ marginTop: 8, background: '#f6f8fa', border: '1px solid #f0f0f0', padding: 12, borderRadius: 6, whiteSpace: 'pre-wrap' }}>
                              {pretty}
                            </pre>
                          </details>
                        );
                      }
                      return <div style={{ whiteSpace: 'pre-wrap' }}>{raw}</div>;
                    })()
                  ) : (
                    <Empty description="No raw message" />
                  )
                ) : (
                  <Empty description="No data yet" />
                )}
              </Card>

              <Card size="small" title={`Evidence (${evidenceCount})`}>
                {observableEvidenceItems.length ? (
                  <List
                    dataSource={observableEvidenceItems.slice(0, 6)}
                    renderItem={(item) => (
                      <List.Item>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <Typography.Text strong>{item.type}</Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                            {item.value}
                          </Typography.Text>
                        </div>
                      </List.Item>
                    )}
                  />
                ) : evidenceItems.length ? (
                  <List
                    dataSource={evidenceItems}
                    renderItem={(item) => (
                      <List.Item>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <Typography.Text strong>{item.file_name}</Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {item.uploaded_time}
                          </Typography.Text>
                        </div>
                      </List.Item>
                    )}
                  />
                ) : (
                  <Empty description="No evidence yet" />
                )}
              </Card>

              <Card size="small">
                <Input.TextArea
                  value={commentDraft}
                  onChange={(e) => setCommentDraft(e.target.value)}
                  placeholder="Comment or use suggestions from AI Assistant..."
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  disabled={commentSending}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
                  <Button>Attach</Button>
                  <Button type="primary" onClick={sendComment} loading={commentSending} disabled={!commentDraft.trim() || commentSending}>
                    Send
                  </Button>
                </div>
              </Card>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Card size="small" title="SOC AI Assistant">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 48, height: 48, borderRadius: '50%', background: '#e6f3ff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>AI</div>
                <div>
                  <div style={{ fontWeight: 600 }}>AI Assistant</div>
                  <div style={{ color: 'rgba(0,0,0,0.45)' }}>Alert interpretation and summary</div>
                </div>
              </div>
              <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                <Button type="primary" onClick={runAiAssistant} loading={aiLoading}>
                  Run AI Assistant
                </Button>
                <Button onClick={openChat}>
                  Chat
                </Button>
              </div>
              {aiError ? (
                <>
                  <Divider />
                  <Typography.Text type="danger">{aiError}</Typography.Text>
                </>
              ) : null}
            </Card>

              <Card size="small" title="AI Tasks">
              {aiTasks.length ? (
                <List
                  dataSource={aiTasks}
                  renderItem={(item: any, idx: number) => {
                    const text = item?.detail ? String(item.detail) : String(item || '');
                    const title = item?.title ? String(item.title) : text;
                    const short = title.length > 60 ? `${title.slice(0, 60)}...` : title;
                    const isOpen = expandedTaskIndex === idx;
                    return (
                      <List.Item style={{ alignItems: 'flex-start' }}>
                        <div style={{ width: '100%' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                            <div>
                              <Tag color="green">OK</Tag> {short || '-'}
                            </div>
                            <Button size="small" type="link" onClick={() => setExpandedTaskIndex(isOpen ? null : idx)}>
                              {isOpen ? 'Hide' : 'Details'}
                            </Button>
                          </div>
                          {isOpen ? (
                            <div style={{ marginTop: 6, whiteSpace: 'pre-wrap', color: 'rgba(0,0,0,0.65)' }}>
                              {text || title || '-'}
                            </div>
                          ) : null}
                        </div>
                      </List.Item>
                    );
                  }}
                />
              ) : (
                <Empty description="No AI tasks yet" />
              )}
              <Divider />
              <Typography.Text strong>Add Tasks</Typography.Text>
              <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                {nextTasks.length ? (
                  nextTasks.map((t: any, idx: number) => {
                    const title = t?.title || String(t);
                    const detail = t?.detail ? String(t.detail) : '';
                    const isOpen = expandedNextTaskIndex === idx;
                    return (
                      <div key={`${title}_${idx}`} style={{ display: 'grid', gap: 4 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                          <Checkbox>{title}</Checkbox>
                          {detail ? (
                            <Button size="small" type="link" onClick={() => setExpandedNextTaskIndex(isOpen ? null : idx)}>
                              {isOpen ? 'Hide' : 'Details'}
                            </Button>
                          ) : null}
                        </div>
                        {detail && isOpen ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12, paddingLeft: 24, whiteSpace: 'pre-wrap' }}>
                            {detail}
                          </Typography.Text>
                        ) : null}
                      </div>
                    );
                  })
                ) : (
                  <Checkbox disabled>No suggested tasks</Checkbox>
                )}
              </div>
            </Card>

            </div>
          </div>
        )}
      </div>

      <Modal
        title="Resolve Ticket"
        open={resolveOpen}
        onCancel={() => setResolveOpen(false)}
        onOk={submitResolve}
        confirmLoading={resolveSubmitting}
        okText="Resolve"
        destroyOnClose
      >
        <Form form={resolveForm} layout="vertical">
          <Form.Item label="Event Category" name="event_category">
            <Select
              loading={resolveLoading}
              allowClear
              placeholder="Select category"
              options={(resolveChoices?.event_category_choices || []).map((c: any) => ({
                value: c.value,
                label: c.label,
              }))}
            />
          </Form.Item>
          <Form.Item label="Event Result" name="event_result">
            <Select
              loading={resolveLoading}
              allowClear
              placeholder="Select result"
              options={(resolveChoices?.event_result_choices || []).map((c: any) => ({
                value: c.value,
                label: c.label,
              }))}
            />
          </Form.Item>
          <Form.Item label="Notes" name="notes">
            <Input.TextArea rows={3} placeholder="Optional notes" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`Chat - ${ticket.ticket_number}`}
        open={chatOpen}
        onCancel={() => setChatOpen(false)}
        footer={null}
        width={1040}
        styles={{ body: { maxHeight: '72vh', overflow: 'auto' } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12, background: '#fafafa', maxHeight: 460, overflow: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <Button size="small" onClick={loadMoreChatHistory} loading={chatMoreLoading} disabled={!chatNextBefore}>
                Load earlier
              </Button>
              <Button
                size="small"
                danger
                onClick={async () => {
                  try {
                    await clearSlaTicketAiChatHistory(ticket.ticket_number);
                    setChatMessages([{ role: 'system', content: buildChatContext(), hidden: true }]);
                    setChatNextBefore(null);
                  } catch {
                    message.error('Failed to clear chat history');
                  }
                }}
              >
                Clear history
              </Button>
            </div>
            {chatHistoryLoading ? (
              <Typography.Text type="secondary">Loading chat history...</Typography.Text>
            ) : chatMessages.filter((m) => !m.hidden).length ? (
              <List
                dataSource={chatMessages.filter((m) => !m.hidden)}
                  renderItem={(msg, idx) => (
                  <List.Item>
                    <div style={{ width: '100%' }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {msg.role === 'user' ? 'You' : 'Assistant'}
                      </Typography.Text>
                      <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                        {msg.content}
                      </div>
                      {msg.role === 'assistant' && msg.trace?.length ? (
                        <div style={{ marginTop: 10 }}>
                          <Card
                            size="small"
                            title="Process"
                            extra={(
                              <Button size="small" type="link" onClick={() => {
                                setCollapsedTraces((prev) => ({
                                  ...prev,
                                  [idx]: !prev[idx],
                                }));
                              }}>
                                {collapsedTraces[idx] ? 'Expand' : 'Collapse'}
                              </Button>
                            )}
                          >
                            {collapsedTraces[idx] ? (
                              <div style={{ color: '#8c8c8c' }}>Collapsed</div>
                            ) : (
                            <div style={{ maxHeight: 360, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
                              {msg.trace.map((evt: any, stepIdx: number) => {
                                if (evt.type === 'iteration_start') {
                                  return (
                                    <div key={`it_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #1677ff', background: '#f0f5ff', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>Iteration {evt.iteration}</div>
                                    </div>
                                  );
                                }
                                if (evt.type === 'model_call') {
                                  return (
                                    <div key={`mc_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #8c8c8c', background: '#fafafa', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>Calling AI model...</div>
                                    </div>
                                  );
                                }
                                if (evt.type === 'tool_calls_detected') {
                                  return (
                                    <div key={`tc_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #722ed1', background: '#f9f0ff', borderRadius: 6 }}>
                                      Detected {evt.count || 0} tool call(s)
                                    </div>
                                  );
                                }
                                if (evt.type === 'tool_call') {
                                  return (
                                    <div key={`call_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #fa8c16', background: '#fff7e6', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>Call tool: {evt.tool}</div>
                                      <div style={{ fontSize: 12, color: '#595959', marginTop: 4 }}>{evt.endpoint || evt.source}</div>
                                      <pre style={{ marginTop: 8, background: '#fff', border: '1px solid #f0f0f0', padding: 8, borderRadius: 6, whiteSpace: 'pre', overflow: 'auto' }}>
{JSON.stringify(evt.arguments || {}, null, 2)}
                                      </pre>
                                    </div>
                                  );
                                }
                                if (evt.type === 'tool_result') {
                                  return (
                                    <div key={`res_${stepIdx}`} style={{ padding: 10, borderLeft: `4px solid ${evt.success ? '#52c41a' : '#ff4d4f'}`, background: evt.success ? '#f6ffed' : '#fff1f0', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>Tool {evt.tool} {evt.success ? 'completed' : 'failed'}</div>
                                      <pre style={{ marginTop: 8, background: '#fff', border: '1px solid #f0f0f0', padding: 8, borderRadius: 6, whiteSpace: 'pre', overflow: 'auto' }}>
{String(evt.content || '')}
                                      </pre>
                                      {evt.execution_id ? (
                                        <div style={{ marginTop: 6, fontSize: 12, color: '#8c8c8c' }}>
                                          Execution ID: {evt.execution_id}
                                        </div>
                                      ) : null}
                                    </div>
                                  );
                                }
                                if (evt.type === 'assistant_response') {
                                  return (
                                    <div key={`ar_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #13c2c2', background: '#e6fffb', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>Analysis summary</div>
                                      <div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{evt.content}</div>
                                    </div>
                                  );
                                }
                                if (evt.type === 'analysis_summary') {
                                  return (
                                    <div key={`as_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #722ed1', background: '#f9f0ff', borderRadius: 6 }}>
                                      <div style={{ fontWeight: 600 }}>AI thinking</div>
                                      <div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{evt.content}</div>
                                    </div>
                                  );
                                }
                                if (evt.type === 'model_error') {
                                  return (
                                    <div key={`err_${stepIdx}`} style={{ padding: 10, borderLeft: '4px solid #ff4d4f', background: '#fff1f0', borderRadius: 6 }}>
                                      Model error: {evt.error}
                                    </div>
                                  );
                                }
                                return null;
                              })}
                            </div>
                            )}
                          </Card>
                        </div>
                      ) : null}
                    </div>
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="No messages yet" />
            )}
          </div>
          <Input.TextArea
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Type your message..."
            autoSize={{ minRows: 2, maxRows: 5 }}
            disabled={chatLoading}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Button onClick={() => setChatMessages([{ role: 'system', content: buildChatContext(), hidden: true }])}>
              Reset Context
            </Button>
            <Space>
              <Button onClick={() => setChatOpen(false)}>Close</Button>
              <Button type="primary" onClick={sendChat} loading={chatLoading} disabled={!chatInput.trim()}>
                Send
              </Button>
            </Space>
          </div>
        </div>
      </Modal>
    </div>
  );
}
