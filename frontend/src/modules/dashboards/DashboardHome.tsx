import React, { useEffect, useState } from 'react';
import { Pie } from '@ant-design/charts';
import { Column } from '@ant-design/plots';
import { Card, Statistic, Row, Col, Space, Select, Button, Modal, Form, Input, Switch, message, Spin, Table, Empty, Divider } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { fetchDashboard, getESConfig, setESConfig, getWebhookConfig, setWebhookConfig } from 'services/alerts';
import type { DashboardData } from 'types';

const Dashboard: React.FC = () => {
  const [data, setData] = useState<DashboardData | null>(null);
  // keep last successful data to avoid UI blanking during reloads
  const [displayData, setDisplayData] = useState<DashboardData | null>(null);
  const failuresRef = React.useRef<number>(0);
  const [refreshing, setRefreshing] = React.useState(false);
  const [activePanelRefresh, setActivePanelRefresh] = useState<string | null>(null);

  const CACHE_KEY = 'siem_dashboard_cache_v1';
  const POLL_INTERVAL_MS = 10 * 1000;
  const [esModalVisible, setEsModalVisible] = useState(false);
  const [webhookModalVisible, setWebhookModalVisible] = useState(false);
  const [esConfig, setEsConfigState] = useState<any>(null);
  const [webhookConfig, setWebhookConfigState] = useState<any>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<number | null>(null);
  const [syncDigest, setSyncDigest] = useState<any | null>(null);
  const [syncDigestVisible, setSyncDigestVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [esSaving, setEsSaving] = useState(false);
  const [trendGroupBy, setTrendGroupBy] = useState<'hour' | 'day'>('hour');
  const [scoreGroupBy, setScoreGroupBy] = useState<'hour' | 'day'>('hour');
  const [themeMode, setThemeMode] = useState<'light' | 'dark'>(() => {
    if (typeof window === 'undefined') return 'light';
    try {
      return localStorage.getItem('siem_ui_theme') === 'dark' ? 'dark' : 'light';
    } catch {
      return 'light';
    }
  });
  // Debug helpers to inspect raw displayData when trends show no data
  const [debugModalVisible, setDebugModalVisible] = useState(false);
  const [debugKey, setDebugKey] = useState<string | null>(null);
  const [showSyncDetails, setShowSyncDetails] = useState(false);
  const [esForm] = Form.useForm();
  const openDebugModal = (key: string | null) => { setDebugKey(key); setDebugModalVisible(true); };
  const getDebugContent = () => {
    if (!displayData) return 'No displayData (dashboard failed to load)';
    if (debugKey === 'alert_trend') return JSON.stringify({ alert_trend: displayData.alert_trend, alert_trend_series: displayData.alert_trend_series }, null, 2);
    if (debugKey === 'alert_score_trend') return JSON.stringify({ alert_score_trend: displayData.alert_score_trend, alert_score_trend_series: displayData.alert_score_trend_series }, null, 2);
    return JSON.stringify(displayData, null, 2);
  };

  const bucketizeTimeSeries = (
    series: Record<string, number> | undefined,
    unit: 'hour' | 'day'
  ) => {
    if (!series) return [] as Array<{ time: string; value: number }>;
    const acc: Record<string, number> = {};
    for (const [k, v] of Object.entries(series)) {
      if (!k) continue;
      // Backend hour keys may look like:
      // - 2025-12-16T12
      // - 2025-12-16T12+00:00
      // - 2025-12-16T12:00:00+00:00
      // Normalize to stable "YYYY-MM-DD" or "YYYY-MM-DDTHH" buckets.
      let key = unit === 'day' ? k.slice(0, 10) : k.slice(0, 13);
      if (key.endsWith(':')) key = key.slice(0, -1);
      acc[key] = (acc[key] || 0) + (Number(v) || 0);
    }
    return Object.entries(acc)
      .sort(([a], [b]) => (a > b ? 1 : a < b ? -1 : 0))
      .map(([time, value]) => ({ time, value }));
  };

  const bucketizeStackedSeries = (
    rows: Array<{ time: string; series: string; value: number }> | undefined,
    unit: 'hour' | 'day'
  ) => {
    if (!rows || rows.length === 0) return [] as Array<{ time: string; series: string; value: number }>;
    const acc: Record<string, number> = {};
    for (const r of rows) {
      if (!r?.time) continue;
      let timeKey = unit === 'day' ? r.time.slice(0, 10) : r.time.slice(0, 13);
      if (timeKey.endsWith(':')) timeKey = timeKey.slice(0, -1);
      const seriesKey = r.series || 'unknown';
      const k = `${timeKey}__${seriesKey}`;
      acc[k] = (acc[k] || 0) + (Number(r.value) || 0);
    }
    return Object.entries(acc)
      .map(([k, value]) => {
        const idx = k.indexOf('__');
        return { time: k.slice(0, idx), series: k.slice(idx + 2), value };
      })
      .sort((a, b) => (a.time > b.time ? 1 : a.time < b.time ? -1 : a.series.localeCompare(b.series)));
  };

  const hasEntries = (obj?: Record<string, unknown> | null) => !!obj && Object.keys(obj).length > 0;
  const isDarkTheme = themeMode === 'dark';
  const chartTextColor = isDarkTheme ? '#dbe6ff' : '#1f2d3d';
  const chartSubtleTextColor = isDarkTheme ? '#e7efff' : '#4b5b70';

  const load = async (opts?: { panelKey?: string; showGlobalSpinner?: boolean }) => {
    const panelKey = opts?.panelKey;
    const showGlobalSpinner = opts?.showGlobalSpinner ?? true;
    const isBackground = !!displayData;
    if (showGlobalSpinner && isBackground) {
      setRefreshing(true);
    } else if (showGlobalSpinner) {
      setLoading(true);
    }
    if (panelKey) setActivePanelRefresh(panelKey);
    try {
      let res = await fetchDashboard();
      setData(res);
      // only update the displayData when we successfully fetched something
      if (res) {
        setDisplayData(res);
        // debug: log keys present in response (helpful to check if trend fields exist)
        try { console.debug('Dashboard load: available keys', Object.keys(res || {})); } catch (e) {}
        // cache for faster next-loads
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), data: res }));
        } catch (err) {
          // ignore storage errors
        }
        failuresRef.current = 0;
      }
    } catch (e:any) {
      console.error('Dashboard load error', e);
      // light failure backoff: increment failures and skip next few polls if failing repeatedly
      failuresRef.current = (failuresRef.current || 0) + 1;
      const failCount = failuresRef.current;
      // only show a visible error when we have no cached data (initial load)
      if (!displayData && failCount <= 1) {
        message.error('Failed to load dashboard data');
      }
      // if we exceed 5 failures, schedule a delayed retry
      if (failCount > 5) {
        setTimeout(() => load(), Math.min(60000, 2000 * Math.pow(2, failCount - 5)));
      }
    } finally {
      if (showGlobalSpinner) {
        if (isBackground) setRefreshing(false);
        else setLoading(false);
      }
      if (panelKey) setActivePanelRefresh(null);
    }
  };

  const loadConfigs = async () => {
    try {
      const es = await getESConfig();
      setEsConfigState(es);
    } catch (e) {
      // ignore
    }
    try {
      const wh = await getWebhookConfig();
      setWebhookConfigState(wh);
    } catch (e) {
      // ignore
    }
  }

  // small hook to animate numbers smoothly between updates
  function useAnimatedNumber(target: number, duration = 500) {
    const [value, setValue] = React.useState(target);
    const rafRef = React.useRef<number | null>(null);
    React.useEffect(() => {
      const start = value;
      const change = target - start;
      if (change === 0) return;
      const startTime = performance.now();
      function animate(now: number) {
        const elapsed = now - startTime;
        if (elapsed >= duration) {
          setValue(target);
          return;
        }
        setValue(start + change * (elapsed / duration));
        rafRef.current = requestAnimationFrame(animate);
      }
      rafRef.current = requestAnimationFrame(animate);
      return () => {
        if (rafRef.current) cancelAnimationFrame(rafRef.current!);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [target]);
    return Math.round(value);
  }

  useEffect(() => {
    // on mount only: read cache and perform initial loads and polling
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { ts: number; data: DashboardData };
        // accept cache younger than 2 minutes
        if (Date.now() - parsed.ts < 2 * 60 * 1000) {
          setDisplayData(parsed.data);
        }
      }
    } catch (err) {
      // ignore cache errors
    }

    // initial load and config load
    load();
    loadConfigs();

    const syncTheme = () => {
      try {
        setThemeMode(localStorage.getItem('siem_ui_theme') === 'dark' ? 'dark' : 'light');
      } catch {
        setThemeMode('light');
      }
    };
    const onThemeChanged = () => syncTheme();
    window.addEventListener('siem_theme_changed', onThemeChanged as EventListener);
    window.addEventListener('storage', onThemeChanged);

    // Polling: only poll when the tab is visible to avoid extra work
    const intervalFn = () => {
      if (document.visibilityState === 'visible') {
        load();
      }
    };
    const id = setInterval(intervalFn, POLL_INTERVAL_MS);

    // also reload once when tab becomes visible
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        load();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('siem_theme_changed', onThemeChanged as EventListener);
      window.removeEventListener('storage', onThemeChanged);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openEsModal = () => setEsModalVisible(true);
  const openWebhookModal = () => setWebhookModalVisible(true);

  useEffect(() => {
    if (!esModalVisible) return;
    const cfg = esConfig || {};
    const schedule = cfg.schedule || {};
    esForm.setFieldsValue({
      enabled: !!cfg.enabled,
      hosts: cfg.hosts || '',
      index: cfg.index || 'alerts',
      username: cfg.username || '',
      password: '',
      use_ssl: !!cfg.use_ssl,
      verify_certs: cfg.verify_certs !== false,
      auto_sync: !!schedule.enabled,
      interval_seconds: Number(schedule.interval_seconds || 300),
      batch_size: Number(schedule.batch_size || 500),
      fetch_all: !!schedule.fetch_all,
    });
    setSelectedHistoryId(null);
  }, [esModalVisible, esConfig, esForm]);

  const onSelectHistoryConnector = (id: number | null) => {
    if (!id) {
      setSelectedHistoryId(null);
      return;
    }
    const history = (esConfig?.history || []) as any[];
    const picked = history.find((h) => Number(h.id) === Number(id));
    if (!picked) return;
    esForm.setFieldsValue({
      enabled: true,
      hosts: picked.hosts || '',
      index: picked.index || 'alerts',
      username: picked.username || '',
      password: '',
      use_ssl: !!picked.use_ssl,
      verify_certs: picked.verify_certs !== false,
    });
    setSelectedHistoryId(Number(id));
    message.success('Connector filled from history. Click Save to switch and sync.');
  };

  const onEsSave = async (values:any) => {
    // Close immediately so large-index sync does not block the modal UX.
    setEsModalVisible(false);
    setEsSaving(true);
    try {
      const payload = { ...values } as any;
      if (selectedHistoryId) payload.history_id = selectedHistoryId;
      if (!String(payload.password || '').trim()) delete payload.password;
      payload.schedule = {
        enabled: !!payload.auto_sync,
        interval_seconds: Number(payload.interval_seconds || 300),
        batch_size: Number(payload.batch_size || 500),
        fetch_all: !!payload.fetch_all,
      };
      delete payload.auto_sync;
      delete payload.interval_seconds;
      delete payload.batch_size;
      delete payload.fetch_all;
      const res = await setESConfig(payload);
      setEsConfigState(res);
      const sync = (res as any)?.sync;
      if (sync?.es_total != null && sync?.db_total != null && Number(sync.db_total) < Number(sync.es_total)) {
        message.warning(`ES sync incomplete: ES has ${sync.es_total}, DB has ${sync.db_total}. Check backend logs.`);
      } else if (sync?.fetched === 0) {
        message.warning('ES config saved, but sync fetched 0 docs. Check backend logs for diagnostics.');
      } else if (sync?.index_table?.ok) {
        message.success(`ES config saved. Synced ${sync.fetched} docs and updated table ${sync.index_table.table}`);
      } else {
        const esTotal = sync?.es_total ?? 'N/A';
        const dbTotal = sync?.db_total ?? 'N/A';
        message.success(`ES config saved. ES total: ${esTotal}, DB total: ${dbTotal}`);
      }
      setSyncDigest(sync || null);
      setShowSyncDetails(false);
      setSyncDigestVisible(true);
      try {
        window.dispatchEvent(
          new CustomEvent('siem_es_connector_switched', {
            detail: { index: (res as any)?.index || values?.index || null },
          })
        );
      } catch {
        // ignore event dispatch failures
      }
      try {
        localStorage.removeItem(CACHE_KEY);
      } catch {
        // ignore cache clear failures
      }
      setData(null);
      setDisplayData(null);
      await loadConfigs();
      await load();
    } catch (e:any) {
      console.error(e);
      const detail = e?.response?.data?.detail || e?.message || 'Failed to save ES config';
      message.error(detail);
      // Re-open modal on save failure so user can retry quickly.
      setEsModalVisible(true);
    } finally {
      setEsSaving(false);
    }
  }

  const onWebhookSave = async (values:any) => {
    try {
      // parse headers if it's a string from textarea
      if (typeof values.headers === 'string') {
        try {
          values.headers = JSON.parse(values.headers || '{}');
        } catch (err) {
          message.error('Headers is not valid JSON');
          return;
        }
      }
      const res = await setWebhookConfig(values);
      setWebhookConfigState(res);
      setWebhookModalVisible(false);
      message.success('Webhook config saved');
    } catch (e:any) {
      console.error(e);
      const detail = e?.response?.data?.detail || e?.message || 'Failed to save webhook config';
      message.error(detail);
    }
  }

  const categoryBreakdown = displayData?.category_breakdown;
  const severityDistribution = displayData?.severity_distribution;
  const panelRefresh = (panelKey: string, ariaLabel = 'Refresh panel') => (
    <Button
      size="small"
      type="text"
      icon={<ReloadOutlined />}
      onClick={() => load({ panelKey, showGlobalSpinner: false })}
      loading={activePanelRefresh === panelKey}
      aria-label={ariaLabel}
    />
  );

  return (
  <>
      <Space style={{ display: 'flex', justifyContent: 'space-between', marginTop: 0, marginBottom: 6, width: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>Dashboard Overview</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <div>
            <Button onClick={openEsModal} style={{ marginRight: 8 }}>ES Settings</Button>
            <Button onClick={openWebhookModal}>Webhook Settings</Button>
          </div>
          {refreshing && <Spin size="small" style={{ marginLeft: 12 }} />}
        </div>
      </Space>

      {/* Keep 5 KPI cards in one row with equal widths that auto-shrink together. */}
      <div style={{ marginTop: 10, width: '100%', paddingBottom: 2 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 16, width: '100%' }}>
          <Card title="Alerts in Last Hour" extra={panelRefresh('kpi_last_hour', 'Refresh Alerts in Last Hour')}>
            <Statistic
              value={useAnimatedNumber((displayData?.recent_1h_alerts ?? 0) as number, 600)}
              loading={!displayData && loading}
              valueStyle={{ transition: 'all 0.5s cubic-bezier(.08,.82,.17,1)' }}
            />
          </Card>
          <Card title="Total Alerts" extra={panelRefresh('kpi_total_alerts', 'Refresh Total Alerts')}>
            <Statistic
              value={useAnimatedNumber(displayData?.total ?? 0, 600)}
              loading={!displayData && loading}
              valueStyle={{ transition: 'all 0.5s cubic-bezier(.08,.82,.17,1)' }}
            />
          </Card>
          <Card title="Data Sources" extra={panelRefresh('kpi_data_sources', 'Refresh Data Sources')}>
            <Statistic
              value={useAnimatedNumber((displayData?.data_source_count ?? 0) as number, 600)}
              loading={!displayData && loading}
              valueStyle={{ transition: 'all 0.5s cubic-bezier(.08,.82,.17,1)' }}
            />
          </Card>
          <Card title="Enabled SIEM Rules" extra={panelRefresh('kpi_enabled_rules', 'Refresh Enabled SIEM Rules')}>
            <Statistic
              value={useAnimatedNumber((displayData?.enabled_siem_rule_count ?? 0) as number, 600)}
              loading={!displayData && loading}
              valueStyle={{ transition: 'all 0.5s cubic-bezier(.08,.82,.17,1)' }}
            />
          </Card>
          <Card title="Detections (Last Hour)" extra={panelRefresh('kpi_detections_1h', 'Refresh Detections in Last Hour')}>
            <Statistic
              value={useAnimatedNumber((displayData?.siem_rule_detected_count_1h ?? 0) as number, 600)}
              loading={!displayData && loading}
              valueStyle={{ transition: 'all 0.5s cubic-bezier(.08,.82,.17,1)' }}
            />
          </Card>
        </div>
      </div>

      {/* Row 1: Pie#1 + Bar#1 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={10}>
          <Card title="Alert Category Breakdown" extra={panelRefresh('category_breakdown', 'Refresh Alert Category Breakdown')}>
            {hasEntries(categoryBreakdown) ? (
              <Pie
                key={`category-pie-${themeMode}`}
                data={Object.entries(categoryBreakdown as Record<string, number>).map(([type, value]) => ({ type, value }))}
                angleField="value"
                colorField="type"
                radius={0.8}
                height={320}
                padding={[12, 12, 72, 12]}
                label={false}
                legend={{
                  color: {
                    title: false,
                    position: 'bottom',
                    rowPadding: 8,
                    itemMarkerSize: 10,
                    itemLabelFill: chartTextColor,
                    itemValueFill: chartSubtleTextColor,
                  },
                }}
                tooltip={{
                  title: (d: any) => `${d?.type ?? ''}`,
                  items: [
                    (d: any) => ({ name: 'Count', value: String(d?.value ?? 0) }),
                  ],
                }}
              />
            ) : (
              <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Empty description="No category data" />
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          {displayData && (
            <Card
              title="Alert Trend"
              extra={
                <Space>
                  {panelRefresh('alert_trend', 'Refresh Alert Trend')}
                  <span style={{ color: chartTextColor }}>Group by</span>
                  <Select
                    size="small"
                    value={trendGroupBy}
                    onChange={(v) => setTrendGroupBy(v as 'hour' | 'day')}
                    style={{ width: 120 }}
                    options={[
                      { label: '1 hour', value: 'hour' },
                      { label: '1 day', value: 'day' },
                    ]}
                  />
                  <Button size="small" onClick={() => openDebugModal('alert_trend')}>View data</Button>
                </Space>
              }
            >
              {displayData?.alert_trend_series && displayData.alert_trend_series.length > 0 ? (
                <Column
                  data={bucketizeStackedSeries(displayData.alert_trend_series, trendGroupBy).map((r) => ({
                    time: r.time,
                    severity: r.series,
                    count: r.value,
                  }))}
                  xField="time"
                  yField="count"
                  colorField="severity"
                  stack={{
                    // bottom -> top
                    orderBy: (d: any) =>
                      ({ low: 0, medium: 1, high: 2, critical: 3, unknown: 4 } as any)[
                        String(d?.severity ?? 'unknown').toLowerCase()
                      ] ?? 99,
                  }}
                  scale={{
                    x: { type: 'band' },
                    color: {
                      domain: ['low', 'medium', 'high', 'critical', 'unknown'],
                      range: ['#1677ff', '#fadb14', '#fa8c16', '#ff4d4f', '#8c8c8c'],
                    },
                  }}
                  height={320}
                  label={false}
                  tooltip={{ showMarkers: false }}
                  axis={{
                    x: {
                      title: false,
                      labelFill: chartTextColor,
                      labelAutoRotate: true,
                      labelFormatter: (v: any) => {
                        const s = String(v ?? '');
                        // show timestamp under x-axis
                        if (s.includes('T')) return s.replace('T', ' ') + ':00';
                        return s;
                      },
                    },
                    y: { title: false, labelFill: chartTextColor },
                  }}
                  legend={{
                    color: {
                      title: false,
                      position: 'top',
                      itemLabelFill: chartTextColor,
                    },
                  }}
                />
              ) : (displayData?.alert_trend && Object.keys(displayData.alert_trend).length > 0) ? (
                <Column
                  data={bucketizeTimeSeries(displayData.alert_trend, trendGroupBy).map((d) => ({ time: d.time, count: d.value }))}
                  xField="time"
                  yField="count"
                  colorField="time"
                  legend={false}
                  height={320}
                  label={false}
                  tooltip={{ showMarkers: false }}
                  axis={{ x: false }}
                />
              ) : (
                <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Empty description="No trend data" />
                </div>
              )}
            </Card>
          )}
        </Col>
      </Row>

      {/* Row 2: Pie#2 + Bar#2 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={10}>
          <Card title="Alert Severity Distribution" extra={panelRefresh('severity_distribution', 'Refresh Alert Severity Distribution')}>
            {hasEntries(severityDistribution) ? (
              <Pie
                key={`severity-pie-${themeMode}`}
                data={Object.entries(severityDistribution as Record<string, number>).map(([type, value]) => ({ type, value }))}
                angleField="value"
                colorField="type"
                radius={0.8}
                height={320}
                padding={[12, 12, 72, 12]}
                label={false}
                legend={{
                  color: {
                    title: false,
                    position: 'bottom',
                    rowPadding: 8,
                    itemMarkerSize: 10,
                    itemLabelFill: chartTextColor,
                    itemValueFill: chartSubtleTextColor,
                  },
                }}
                tooltip={{
                  title: (d: any) => `${d?.type ?? ''}`,
                  items: [
                    (d: any) => ({ name: 'Count', value: String(d?.value ?? 0) }),
                  ],
                }}
              />
            ) : (
              <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Empty description="No severity data" />
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          {displayData && (
            <Card
              title="Alert Score Trend"
              extra={
                <Space>
                  {panelRefresh('alert_score_trend', 'Refresh Alert Score Trend')}
                  <span style={{ color: chartTextColor }}>Group by</span>
                  <Select
                    size="small"
                    value={scoreGroupBy}
                    onChange={(v) => setScoreGroupBy(v as 'hour' | 'day')}
                    style={{ width: 120 }}
                    options={[
                      { label: '1 hour', value: 'hour' },
                      { label: '1 day', value: 'day' },
                    ]}
                  />
                  <Button size="small" onClick={() => openDebugModal('alert_score_trend')}>View data</Button>
                </Space>
              }
            >
              {displayData?.alert_score_trend_series && displayData.alert_score_trend_series.length > 0 ? (
                <Column
                  data={bucketizeStackedSeries(displayData.alert_score_trend_series, scoreGroupBy).map((r) => ({
                    time: r.time,
                    severity: r.series,
                    score: r.value,
                  }))}
                  xField="time"
                  yField="score"
                  colorField="severity"
                  stack={{
                    // bottom -> top
                    orderBy: (d: any) =>
                      ({ low: 0, medium: 1, high: 2, critical: 3, unknown: 4 } as any)[
                        String(d?.severity ?? 'unknown').toLowerCase()
                      ] ?? 99,
                  }}
                  scale={{
                    x: { type: 'band' },
                    color: {
                      domain: ['low', 'medium', 'high', 'critical', 'unknown'],
                      range: ['#1677ff', '#fadb14', '#fa8c16', '#ff4d4f', '#8c8c8c'],
                    },
                  }}
                  height={320}
                  label={false}
                  tooltip={{ showMarkers: false }}
                  axis={{
                    x: {
                      title: false,
                      labelFill: chartTextColor,
                      labelAutoRotate: true,
                      labelFormatter: (v: any) => {
                        const s = String(v ?? '');
                        if (s.includes('T')) return s.replace('T', ' ') + ':00';
                        return s;
                      },
                    },
                    y: { title: false, labelFill: chartTextColor },
                  }}
                  legend={{
                    color: {
                      title: false,
                      position: 'top',
                      itemLabelFill: chartTextColor,
                    },
                  }}
                />
              ) : (displayData?.alert_score_trend && Object.keys(displayData.alert_score_trend).length > 0) ? (
                <Column
                  data={bucketizeTimeSeries(displayData.alert_score_trend, scoreGroupBy).map((d) => ({ time: d.time, score: d.value }))}
                  xField="time"
                  yField="score"
                  colorField="time"
                  legend={false}
                  height={320}
                  label={false}
                  tooltip={{ showMarkers: false }}
                  axis={{ x: false }}
                />
              ) : (
                <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Empty description="No trend data" />
                </div>
              )}
            </Card>
          )}
        </Col>
      </Row>

      {/* Tables (responsive) */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12} xl={6}>
          <Card
            title="Top Source 10 IP"
            extra={panelRefresh('top_source_ips', 'Refresh Top Source 10 IP')}
            styles={{ body: { padding: '8px 12px 12px' } }}
          >
            <Table
              size="small"
              pagination={false}
              tableLayout="fixed"
              rowKey={(r) => r.name}
              columns={[
                { title: 'IP', dataIndex: 'name', ellipsis: true },
                { title: 'Count', dataIndex: 'count', width: 90, align: 'right' as const },
              ]}
              dataSource={(displayData?.top_source_ips ?? []) as any}
            />
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card
            title="Top 10 Users"
            extra={panelRefresh('top_users', 'Refresh Top 10 Users')}
            styles={{ body: { padding: '8px 12px 12px' } }}
          >
            <Table
              size="small"
              pagination={false}
              tableLayout="fixed"
              rowKey={(r) => r.name}
              columns={[
                { title: 'User', dataIndex: 'name', ellipsis: true },
                { title: 'Count', dataIndex: 'count', width: 90, align: 'right' as const },
              ]}
              dataSource={(displayData?.top_users ?? []) as any}
            />
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card
            title="Top 10 Sources"
            extra={panelRefresh('top_sources', 'Refresh Top 10 Sources')}
            styles={{ body: { padding: '8px 12px 12px' } }}
          >
            <Table
              size="small"
              pagination={false}
              tableLayout="fixed"
              rowKey={(r) => r.name}
              columns={[
                { title: 'Source', dataIndex: 'name', ellipsis: true },
                { title: 'Count', dataIndex: 'count', width: 90, align: 'right' as const },
              ]}
              dataSource={(displayData?.top_sources ?? []) as any}
            />
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card
            title="Top 10 Rules"
            extra={panelRefresh('top_rules', 'Refresh Top 10 Rules')}
            styles={{ body: { padding: '8px 12px 12px' } }}
          >
            <Table
              size="small"
              pagination={false}
              tableLayout="fixed"
              rowKey={(r) => r.name}
              columns={[
                {
                  title: 'Rule',
                  dataIndex: 'name',
                  ellipsis: true,
                  render: (value: string) => (
                    <span style={{ display: 'inline-block', width: '100%', wordBreak: 'break-all' }}>{value}</span>
                  ),
                },
                { title: 'Count', dataIndex: 'count', width: 90, align: 'right' as const },
              ]}
              dataSource={(displayData?.top_rules ?? []) as any}
            />
          </Card>
        </Col>
      </Row>

      <Modal title={`Raw dashboard data ${debugKey ? ` - ${debugKey}` : ''}`} open={debugModalVisible} onCancel={() => setDebugModalVisible(false)} footer={<Button onClick={() => setDebugModalVisible(false)}>Close</Button>} width={800}>
        <pre style={{ maxHeight: '60vh', overflow: 'auto', whiteSpace: 'pre-wrap' }}>{getDebugContent()}</pre>
      </Modal>

      <Modal title="ES Settings" open={esModalVisible} onCancel={() => setEsModalVisible(false)} footer={null}>
        <Form form={esForm} initialValues={esConfig || {enabled: false, hosts: '', index: 'alerts', use_ssl: false, verify_certs: true}} onFinish={onEsSave}>
          <Form.Item name="enabled" valuePropName="checked" label="Enabled">
            <Switch />
          </Form.Item>
          <Form.Item label="Recent connectors">
            <Select
              allowClear
              placeholder="Select previous successful connector"
              value={selectedHistoryId as any}
              onChange={(v) => onSelectHistoryConnector((v as number) || null)}
              options={((esConfig?.history || []) as any[]).map((h: any) => ({
                label: `${h.index || 'alerts'} @ ${h.hosts || ''} (${h.username || 'no-user'})`,
                value: h.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="hosts" label="Hosts">
            <Input placeholder="http://localhost:9200" />
          </Form.Item>
          <Form.Item name="index" label="Index">
            <Input placeholder="alerts" />
          </Form.Item>
          <Form.Item name="username" label="Username">
            <Input />
          </Form.Item>
          <Form.Item name="password" label="Password">
            <Input.Password placeholder={selectedHistoryId ? 'Leave blank to reuse saved password' : ''} />
          </Form.Item>
          <Form.Item name="use_ssl" valuePropName="checked" label="Use SSL">
            <Switch />
          </Form.Item>
          <Form.Item name="verify_certs" valuePropName="checked" label="Verify Certs">
            <Switch />
          </Form.Item>
          <Form.Item name="auto_sync" valuePropName="checked" label="Auto Sync">
            <Switch />
          </Form.Item>
          <Form.Item name="interval_seconds" label="Sync Interval (s)">
            <Input type="number" min={10} />
          </Form.Item>
          <Form.Item name="batch_size" label="Batch Size">
            <Input type="number" min={1} />
          </Form.Item>
          <Form.Item name="fetch_all" valuePropName="checked" label="Fetch All">
            <Switch />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary" loading={esSaving}>Save</Button>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Connector Sync Digest"
        open={syncDigestVisible}
        onCancel={() => setSyncDigestVisible(false)}
        footer={<Button type="primary" onClick={() => setSyncDigestVisible(false)}>Done</Button>}
        width={760}
      >
        <div
          style={{
            borderRadius: 14,
            padding: 16,
            background: 'linear-gradient(135deg, rgba(22,119,255,0.08) 0%, rgba(56,189,248,0.08) 50%, rgba(34,197,94,0.08) 100%)',
            border: '1px solid rgba(22,119,255,0.18)',
            marginBottom: 12,
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 700 }}>Index: {syncDigest?.index || '-'}</div>
          <div style={{ marginTop: 4, color: '#64748b' }}>
            Source: {syncDigest?.source || '-'} • Duration: {syncDigest?.duration_ms ?? 0} ms
          </div>
        </div>
        <Row gutter={[12, 12]}>
          {[
            ['Fetched', syncDigest?.fetched ?? 0],
            ['Inserted', syncDigest?.inserted ?? 0],
            ['Updated', syncDigest?.updated ?? 0],
            ['Unchanged', syncDigest?.unchanged ?? 0],
            ['Skipped', syncDigest?.skipped ?? 0],
            ['DB Total', syncDigest?.db_total ?? 0],
            ['ES Total', syncDigest?.es_total ?? 'N/A'],
          ].map(([name, value]) => (
            <Col xs={12} md={8} key={String(name)}>
              <Card size="small" styles={{ body: { padding: 12 } }}>
                <div style={{ fontSize: 12, color: '#64748b' }}>{String(name)}</div>
                <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>{String(value)}</div>
              </Card>
            </Col>
          ))}
        </Row>
        <Divider style={{ margin: '12px 0' }} />
        <Button size="small" onClick={() => setShowSyncDetails((s) => !s)}>
          {showSyncDetails ? 'Hide details' : 'View details'}
        </Button>
        {showSyncDetails && (
          <pre style={{ marginTop: 10, maxHeight: '34vh', overflow: 'auto', whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(syncDigest || {}, null, 2)}
          </pre>
        )}
      </Modal>

      <Modal title="Webhook Settings" open={webhookModalVisible} onCancel={() => setWebhookModalVisible(false)} footer={null}>
        <Form initialValues={webhookConfig || {url: '', method: 'POST', headers: {}, active: true}} onFinish={onWebhookSave}>
          <Form.Item name="url" label="URL" rules={[{ required: true, message: 'Please enter URL' }]}> <Input /> </Form.Item>
          <Form.Item name="method" label="Method"> <Input /> </Form.Item>
          <Form.Item name="active" valuePropName="checked" label="Active"> <Switch /> </Form.Item>
          <Form.Item name="headers" label="Headers (JSON)"> <Input.TextArea placeholder='{"Authorization":"Bearer ..."}' /> </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary">Save</Button>
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

export default Dashboard;
