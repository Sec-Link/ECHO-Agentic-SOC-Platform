'use client';

import React from 'react';
import DashboardList from '../../../modules/dashboards/DashboardList';

export default function DashboardsPage() {
  // Editor page has been removed; keep dashboards list accessible.
  return <DashboardList onEdit={() => {}} />;
}
