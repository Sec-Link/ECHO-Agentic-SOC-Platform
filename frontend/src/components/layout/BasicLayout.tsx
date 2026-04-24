'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Layout, Card, ConfigProvider, theme as antdTheme } from 'antd';
import { usePathname, useRouter } from 'next/navigation';
import { clearAccessToken, getRbacMe } from 'services/accounts';
import { permissionByKey, resolveRouteKey, type RouteKey } from '../../route';
import HeaderBar from './Header';
import Sidebar from './Sidebar';

const { Header, Content } = Layout;

export default function BasicLayout({
  children,
  onLoggedOut,
}: {
  children: React.ReactNode;
  onLoggedOut?: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname() || '/dashboard';

  const [username, setUsername] = useState<string | null>(null);
  const [rbacMe, setRbacMe] = useState<any | null>(null);
  const [effectivePermissions, setEffectivePermissions] = useState<string[]>([]);

  const [siderWidth, setSiderWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return 260;
    try {
      const raw = localStorage.getItem('siem_sider_width');
      const val = raw ? Number(raw) : NaN;
      if (!Number.isFinite(val)) return 260;
      return Math.min(340, Math.max(220, val));
    } catch {
      return 260;
    }
  });
  const [siderCollapsed, setSiderCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    try {
      return localStorage.getItem('siem_sider_collapsed') === '1';
    } catch {
      return false;
    }
  });
  const [siderWidthCustomized, setSiderWidthCustomized] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    try {
      return localStorage.getItem('siem_sider_width') != null;
    } catch {
      return false;
    }
  });
  const [isResizing, setIsResizing] = useState(false);
  const [openKeys, setOpenKeys] = useState<string[]>(['monitorGroup']);
  const [uiTheme, setUiTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window === 'undefined') return 'light';
    try {
      return localStorage.getItem('siem_ui_theme') === 'dark' ? 'dark' : 'light';
    } catch {
      return 'light';
    }
  });

  const [impersonation, setImpersonation] = useState<{
    userId: number;
    username: string;
    permissions: string[];
  } | null>(() => {
    if (typeof window === 'undefined') return null;
    try {
      const raw = localStorage.getItem('siem_impersonation');
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  const { key: selectedKey } = resolveRouteKey(pathname);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const u = localStorage.getItem('siem_username');
      if (u) setUsername(u);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    const syncImpersonation = () => {
      try {
        const raw = localStorage.getItem('siem_impersonation');
        setImpersonation(raw ? JSON.parse(raw) : null);
      } catch {
        setImpersonation(null);
      }
    };
    syncImpersonation();
    window.addEventListener('storage', syncImpersonation);
    window.addEventListener('siem_impersonation_changed', syncImpersonation as EventListener);
    return () => {
      window.removeEventListener('storage', syncImpersonation);
      window.removeEventListener('siem_impersonation_changed', syncImpersonation as EventListener);
    };
  }, []);

  useEffect(() => {
    const loadMe = async () => {
      try {
        const res = await getRbacMe();
        setRbacMe(res);
      } catch {
        setRbacMe(null);
      }
    };
    loadMe();
  }, []);

  useEffect(() => {
    if (impersonation && Array.isArray(impersonation.permissions)) {
      setEffectivePermissions(impersonation.permissions);
    } else {
      setEffectivePermissions(Array.isArray(rbacMe?.permissions) ? rbacMe.permissions : []);
    }
  }, [rbacMe, impersonation]);

  useEffect(() => {
    try {
      localStorage.setItem('siem_sider_width', String(siderWidth));
    } catch {}
  }, [siderWidth]);

  useEffect(() => {
    try {
      localStorage.setItem('siem_sider_collapsed', siderCollapsed ? '1' : '0');
    } catch {}
  }, [siderCollapsed]);

  useEffect(() => {
    try {
      localStorage.setItem('siem_ui_theme', uiTheme);
    } catch {}
  }, [uiTheme]);

  useEffect(() => {
    if (!isResizing) return;
    const onMove = (e: MouseEvent) => {
      const next = Math.min(340, Math.max(220, e.clientX));
      setSiderWidth(next);
    };
    const onUp = () => setIsResizing(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // keep sensible parent open when a child is selected
  useEffect(() => {
    if (['dashboard', 'alerts'].includes(selectedKey)) setOpenKeys(['monitorGroup']);
    else if (['tickets', 'assets'].includes(selectedKey)) setOpenKeys(['investigationGroup']);
    else if (['integrations', 'datasources', 'dashboards'].includes(selectedKey)) setOpenKeys(['dataPlatformGroup']);
    else if (['correlation', 'orchestrator', 'interfaces', 'workflows', 'workflow-executions'].includes(selectedKey)) {
      setOpenKeys(['automationGroup']);
    } else if (['permissions', 'ai-assistant', 'registration-approvals', 'audit-logs'].includes(selectedKey)) {
      setOpenKeys(['systemManagementGroup']);
    } else {
      setOpenKeys(['monitorGroup']);
    }
  }, [selectedKey]);

  const canAccess = (perm?: string) => {
    if (!perm) return true;
    if (!rbacMe && !impersonation) return true;
    if (!impersonation && rbacMe?.is_superuser) return true;
    return effectivePermissions.includes(perm);
  };

  const settingsItems = useMemo(
    () =>
      [
        { key: 'integrations' as RouteKey, label: 'Integrations' },
        { key: 'dashboards' as RouteKey, label: 'Dashboard List' },
        { key: 'datasources' as RouteKey, label: 'Data Sources' },
        { key: 'orchestrator' as RouteKey, label: 'Orchestrator' },
        { key: 'interfaces' as RouteKey, label: 'Interfaces' },
        { key: 'correlation' as RouteKey, label: 'Correlation' },
        { key: 'workflows' as RouteKey, label: 'Workflows' },
        { key: 'workflow-executions' as RouteKey, label: 'Workflow Executions' },
        { key: 'permissions' as RouteKey, label: 'Access Management' },
        { key: 'registration-approvals' as RouteKey, label: 'Registration Approvals' },
        { key: 'audit-logs' as RouteKey, label: 'Audit Logs' },
        { key: 'ai-assistant' as RouteKey, label: 'AI Assistant' },
      ].filter((item) => canAccess(permissionByKey[item.key])),
    [effectivePermissions, impersonation, rbacMe]
  );

  const autoSiderWidth = useMemo(() => {
    const baseLabels = [
      'Dashboard',
      'Alerts',
      'Tickets',
      'Settings',
      'Monitoring',
      'Investigation',
      'Detection & Automation',
      'Access Management',
      'Correlation',
      'Orchestrator',
      'Interfaces',
      'Workflow Executions',
      'Data Sources',
      'Dashboard List',
      'Integrations',
    ];
    const dynamicLabels = settingsItems.map((i) => i.label);
    const labels = [...baseLabels, ...dynamicLabels];
    const longest = labels.reduce((max, label) => Math.max(max, label.length), 0);
    return Math.min(340, Math.max(240, longest * 8 + 96));
  }, [settingsItems]);

  useEffect(() => {
    if (!siderWidthCustomized) setSiderWidth(autoSiderWidth);
  }, [autoSiderWidth, siderWidthCustomized]);

  const handleLogout = () => {
    try {
      localStorage.removeItem('siem_access_token');
      localStorage.removeItem('siem_username');
      localStorage.removeItem('siem_impersonation');
    } catch {}
    clearAccessToken();
    setUsername(null);
    setImpersonation(null);
    if (onLoggedOut) onLoggedOut();
    router.push('/');
    router.refresh();
  };

  const handleToggleTheme = () => {
    setUiTheme((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem('siem_ui_theme', next);
      } catch {}
      window.dispatchEvent(new CustomEvent('siem_theme_changed', { detail: { theme: next } }));
      return next;
    });
  };

  const clearImpersonation = () => {
    try {
      localStorage.removeItem('siem_impersonation');
    } catch {}
    setImpersonation(null);
    window.dispatchEvent(new Event('siem_impersonation_changed'));
  };

  const renderDenied = () => (
    <Card title="Access denied">
      <div>This view does not have permission to access this feature.</div>
    </Card>
  );

  const currentPerm = permissionByKey[selectedKey];
  const content = canAccess(currentPerm) ? children : renderDenied();
  const antdThemeConfig = useMemo(
    () =>
      uiTheme === 'dark'
        ? {
            algorithm: antdTheme.darkAlgorithm,
            token: {
              colorPrimary: '#3d7cff',
              colorBgBase: '#000000',
              colorBgLayout: '#000000',
              colorBgContainer: '#0b1426',
              colorText: '#dbe6ff',
              colorTextSecondary: '#a9b6d3',
              colorBorder: '#2a3f67',
            },
            components: {
              Layout: { bodyBg: '#000000', headerBg: '#060b18', siderBg: '#060b18' },
              Card: { colorBgContainer: '#0b1426' },
              Modal: { contentBg: '#0b1426', headerBg: '#0b1426' },
              Table: { colorBgContainer: '#0b1426', headerBg: '#101c33' },
              Input: { colorBgContainer: '#0f1b31', activeBorderColor: '#4b89ff' },
              Select: { colorBgContainer: '#0f1b31', optionSelectedBg: '#1a2d4d' },
              Menu: { itemSelectedBg: 'rgba(173, 198, 255, 0.22)', itemHoverBg: 'rgba(173, 198, 255, 0.14)' },
            },
          }
        : {
            algorithm: antdTheme.defaultAlgorithm,
            token: {
              colorPrimary: '#1f6fd1',
              colorBgLayout: '#f5faff',
            },
          },
    [uiTheme]
  );

  return (
    <ConfigProvider theme={antdThemeConfig}>
      <Layout
        className={uiTheme === 'dark' ? 'theme-dark' : 'theme-light'}
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'flex-start',
          background: 'var(--bg-main)',
          transition: 'background-color 180ms ease',
        }}
      >
        <style>{`
        .siem-menu-pale .ant-menu-item, .siem-menu-pale .ant-menu-submenu-title {
          color: var(--text-primary);
          border-radius: 10px;
          margin-inline: 8px;
          width: calc(100% - 16px);
          transition: background-color 190ms ease, transform 190ms ease, box-shadow 190ms ease;
        }
        .siem-menu-pale .ant-menu-item .anticon, .siem-menu-pale .ant-menu-submenu-title .anticon { color: var(--text-primary); }
        .siem-menu-pale .ant-menu-title-content {
          white-space: nowrap;
          font-family: "IBM Plex Sans", "JetBrains Mono", "Segoe UI", sans-serif;
          letter-spacing: 0.22px;
          font-size: 13.6px;
          line-height: 1.3;
          text-rendering: geometricPrecision;
        }
        .siem-menu-pale .ant-menu-sub.ant-menu-inline { background: transparent !important; }
        .siem-menu-pale .ant-menu-item:hover, .siem-menu-pale .ant-menu-item-active, .siem-menu-pale .ant-menu-item-selected, .siem-menu-pale .ant-menu-submenu-title:hover {
          background: var(--menu-hover-bg) !important;
          color: var(--text-primary) !important;
          transform: translateX(2px);
        }
        .siem-menu-pale .ant-menu-item-selected {
          background: var(--menu-selected-bg) !important;
          box-shadow: inset 2px 0 0 var(--menu-selected-edge);
        }
        `}</style>
        <Sidebar
          siderWidth={siderWidth}
          siderCollapsed={siderCollapsed}
          openKeys={openKeys}
          selectedKey={selectedKey}
          settingsItems={settingsItems}
          setSiderCollapsed={setSiderCollapsed}
          setOpenKeys={setOpenKeys}
          setIsResizing={setIsResizing}
          setSiderWidthCustomized={setSiderWidthCustomized}
          canAccess={canAccess}
          onNavigate={(path) => router.push(path)}
        />

        <Layout style={{ flex: 1, minWidth: 0, background: 'var(--bg-main)', transition: 'background-color 180ms ease' }}>
          <Header style={{ background: 'var(--bg-header)', padding: 0, borderBottom: 'none', color: 'var(--text-primary)' }}>
            <HeaderBar
              username={username}
              impersonation={impersonation}
              isReadonly={!!rbacMe?.is_readonly}
              isDarkTheme={uiTheme === 'dark'}
              onToggleTheme={handleToggleTheme}
              onOpenProfile={() => router.push('/profile')}
              onClearImpersonation={clearImpersonation}
              onLogout={handleLogout}
            />
          </Header>
          <Content style={{ padding: 24, background: 'var(--bg-main)', color: 'var(--text-primary)' }}>
            <div key={pathname} className="siem-page-transition">
              {content}
            </div>
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}
