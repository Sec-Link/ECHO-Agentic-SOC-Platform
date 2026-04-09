'use client';

import React, { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { App } from 'antd';
import BasicLayout from '../components/layout/BasicLayout';
import LoginForm from '../modules/accounts/components/LoginForm';

function readStoredToken(): string | null {
  try {
    const t = localStorage.getItem('siem_access_token');
    // Migration safety: old JWT tokens contain dots and won't work with TokenAuth.
    if (t && t.includes('.')) {
      localStorage.removeItem('siem_access_token');
      return null;
    }
    return t;
  } catch {
    return null;
  }
}

export default function ClientRoot({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() || '/';
  const [hydrated, setHydrated] = useState(false);
  const [loggedIn, setLoggedIn] = useState<boolean>(false);

  useEffect(() => {
    setLoggedIn(!!readStoredToken());
    setHydrated(true);
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'siem_access_token') setLoggedIn(!!readStoredToken());
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (loggedIn && pathname === '/') {
      router.replace('/dashboard');
    }
  }, [hydrated, loggedIn, pathname, router]);

  if (!hydrated) {
    return null;
  }

  return (
    <App>
      {!loggedIn ? (
        <LoginForm onLogin={() => setLoggedIn(true)} />
      ) : (
        <BasicLayout onLoggedOut={() => setLoggedIn(false)}>{children}</BasicLayout>
      )}
    </App>
  );
}
