import React, { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import GridLayoutLib, { Layout, WidthProvider } from 'react-grid-layout'
const GridLayout = WidthProvider(GridLayoutLib)
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { Button, Space, Modal, DatePicker, InputNumber } from 'antd'
import { Select, message, Input } from 'antd'
import { queryPreview, listDashboards, createDashboard, getDashboard, updateDashboard, deleteDashboard } from 'services/dashboards'
import { listDatasources } from 'services/datasource'
import Panel from './Panel'
import PanelConfigModal from './PanelConfigModal'
import { Column, Bar, Line, Pie, Scatter } from '@ant-design/charts'
import { Table } from 'antd'

// DashboardEditor provides a visual layout editor that supports:
// - Listing and loading existing dashboards
// - Adding/removing panels and configuring SQL queries or bindings
// - Creating panels from SQL previews and populating runtimeData
// Note: dataset-agnostic support was removed for simplicity; runtime data is sourced from SQL previews.
// Uses react-grid-layout for drag/resize and @ant-design/charts for rendering.
export default function DashboardEditor({ dashboardId, onBack }:{ dashboardId?:string, onBack:()=>void }){
  const [isEditMode, setIsEditMode] = useState<boolean>(true)
  const [dashboards, setDashboards] = useState<any[]>([])
  const [layout, setLayout] = useState<Layout[]>([])
  const [panels, setPanels] = useState<any[]>([])
  const [configPanel, setConfigPanel] = useState<any | null>(null)
  const [sqlModalVisible, setSqlModalVisible] = useState(false)
  const [sqlDatasource, setSqlDatasource] = useState<string | null>(null)
  const [sqlText, setSqlText] = useState<string>('')
  const [datasources, setDatasources] = useState<any[]>([])
  const [name, setName] = useState<string>('')
  const [description, setDescription] = useState<string>('')
  const [timestampField, setTimestampField] = useState<string | null>(null)
  const [timeSelector, setTimeSelector] = useState<string | null>(null)
  const [timestampRelative, setTimestampRelative] = useState<string | null>(null)
  const [timestampFrom, setTimestampFrom] = useState<any | null>(null)
  const [timestampTo, setTimestampTo] = useState<any | null>(null)
  const [timestampRelativeCustomValue, setTimestampRelativeCustomValue] = useState<number | null>(null)
  const [timestampRelativeCustomUnit, setTimestampRelativeCustomUnit] = useState<string | null>(null)
  // panels default to simple 'chart' type; removed global chart-type selector

  // Helper to refresh runtimeData for given panels (runs SQL previews where configured)
  async function refreshPanelsFor(panelsToRefresh: any[], override?: { time_range?: { from: string, to: string } | undefined, time_field?: string | null }){
    if(!Array.isArray(panelsToRefresh) || panelsToRefresh.length === 0) return
    for(const p of panelsToRefresh){
      const sql = p.config?.sql
      const ds = p.config?.datasource || p.config?.datasourceId || p.config?.dataset
      if(sql && ds){
        try{
            const tr = override && override.time_range ? [override.time_range.from, override.time_range.to] : computeTimeRangeFromSelector()
            const time_range = tr ? { from: tr[0], to: tr[1] } : undefined
            const tf = override && Object.prototype.hasOwnProperty.call(override, 'time_field') ? override.time_field : timestampField
            console.debug('refreshPanelsFor sending time_range', time_range, 'time_field', tf, 'panel', p.i)
            const previewRes = await queryPreview({ datasource: ds, sql, limit: 200, time_range, time_field: tf })
          const cols = (previewRes.columns || []).map((c:any)=> typeof c === 'string' ? c : c.name)
          const rows = previewRes.rows || []
          const mapped = rows.map((rrow:any[])=>{
            const obj:any = {}
            cols.forEach((c:string|any,i:number)=>{ const name = typeof c === 'string' ? c : c.name; obj[name] = rrow[i] })
            return obj
          })
          setPanels(prev=> (Array.isArray(prev) ? prev.map(pp=> pp.i === p.i ? { ...pp, runtimeData: mapped } : pp) : prev))
        }catch(e:any){
          console.warn('refreshPanelsFor: preview failed for panel', p.i, e)
          try{ console.error('preview error response', e?.response?.data) }catch(err){}
        }
      }
    }
  }

  useEffect(()=>{
    // Initial load: dashboards list and datasources; if dashboardId provided, load its layout and panels
    listDashboards().then(r=>setDashboards(r)).catch(()=>setDashboards([]))
    listDatasources().then(r=>setDatasources(r)).catch(()=>setDatasources([]))
    if(dashboardId){
      getDashboard(dashboardId).then(d=>{
        // back the persisted timestamp/time selector values first
        setName(d.name || '')
        setDescription(d.description || '')
        setTimestampField(d.timestamp_field || d.time_field || null)
        setTimeSelector(d.time_selector || d.timestamp_relative || null)
        setTimestampRelative(d.timestamp_relative || null)
        setTimestampFrom(d.timestamp_from ? dayjs(d.timestamp_from) : null)
        setTimestampTo(d.timestamp_to ? dayjs(d.timestamp_to) : null)
        setTimestampRelativeCustomValue(d.timestamp_relative_custom_value || null)
        setTimestampRelativeCustomUnit(d.timestamp_relative_custom_unit || null)
        // then set panels/layout and refresh panels using the persisted time range
        setLayout(d.layout || [])
        const loadedPanels = d.layout || []
        setPanels(loadedPanels)
        const loadedTimeRange = (d.timestamp_from && d.timestamp_to) ? { from: d.timestamp_from, to: d.timestamp_to } : undefined
        const loadedTimeField = d.timestamp_field || d.time_field || null
        // pass override so refresh uses stored time settings immediately
        refreshPanelsFor(loadedPanels, { time_range: loadedTimeRange, time_field: loadedTimeField })
      }).catch(()=>{})
    }else{
      // seed example
      const seed = [{ i: '1', x: 0, y: 0, w: 6, h: 6, type: 'chart', config: { title: 'Sample', dataset: null } }]
      setLayout(seed)
      setPanels(seed)
      // refresh seed panels as well
      refreshPanelsFor(seed)
    }
  },[dashboardId])

  function onLayoutChange(newLayout: Layout[]){
    setLayout(newLayout)
    setPanels(prev => prev.map(p=>{
      const item = newLayout.find(l=>l.i === p.i)
      return item ? { ...p, x: item.x, y: item.y, w: item.w, h: item.h } : p
    }))
  }

  // compute from/to ISO strings based on the dashboard-level time selector/fields
  function computeTimeRangeFromSelector(): [string,string] | null{
    if(timeSelector === 'absolute'){
      if(!timestampFrom || !timestampTo) return null
      const from = (typeof timestampFrom.toISOString === 'function') ? timestampFrom.toISOString() : (dayjs(timestampFrom).toISOString ? dayjs(timestampFrom).toISOString() : String(timestampFrom))
      const to = (typeof timestampTo.toISOString === 'function') ? timestampTo.toISOString() : (dayjs(timestampTo).toISOString ? dayjs(timestampTo).toISOString() : String(timestampTo))
      return [from, to]
    }
    // presets like '1h','6h','24h','7d'
    if(typeof timeSelector === 'string' && /^(\d+)([hmd])$/.test(timeSelector)){
      // interpret unit: h hours, d days, m minutes
      const m = timeSelector.match(/^(\d+)([hmd])$/)
      if(!m) return null
      const v = Number(m[1])
      const u = m[2]
      const now = new Date()
      let from = new Date(now)
      if(u === 'h') from.setHours(now.getHours() - v)
      else if(u === 'd') from.setDate(now.getDate() - v)
      else if(u === 'm') from.setMinutes(now.getMinutes() - v)
      return [from.toISOString(), now.toISOString()]
    }
    if(timeSelector === 'custom_relative' && timestampRelativeCustomValue && timestampRelativeCustomUnit){
      const now = new Date()
      const from = new Date(now)
      const v = Number(timestampRelativeCustomValue)
      const u = timestampRelativeCustomUnit
      if(u === 'h') from.setHours(now.getHours() - v)
      else if(u === 'd') from.setDate(now.getDate() - v)
      else if(u === 'm') from.setMinutes(now.getMinutes() - v)
      return [from.toISOString(), now.toISOString()]
    }
    // fallback: if timestamp_relative like '1h' stored in timestampRelative
    if(timestampRelative && /^(\d+)([hmd])$/.test(timestampRelative)){
      const m = timestampRelative.match(/^(\d+)([hmd])$/)
      if(!m) return null
      const v = Number(m[1])
      const u = m[2]
      const now = new Date()
      const from = new Date(now)
      if(u === 'h') from.setHours(now.getHours() - v)
      else if(u === 'd') from.setDate(now.getDate() - v)
      else if(u === 'm') from.setMinutes(now.getMinutes() - v)
      return [from.toISOString(), now.toISOString()]
    }
    return null
  }

  function applyTimeRangeToSql(sql: string){
    const tr = computeTimeRangeFromSelector()
    if(!tr) return sql
    const from = tr[0]
    const to = tr[1]
    let out = sql.replace(/\{\{__from\}\}/g, `'${from}'`).replace(/\{\{__to\}\}/g, `'${to}'`)
    out = out.replace(/\{\{__time_range\}\}/g, `(${timestampField || 'time'} >= '${from}' AND ${timestampField || 'time'} <= '${to}')`)
    out = out.replace(/\{\{__time_range:([^}]+)\}\}/g, (_m, fld) => `(${fld} >= '${from}' AND ${fld} <= '${to}')`)
    return out
  }

  function addPanel(){
    const id = String(Date.now())
    const p = { i: id, x: 0, y: Infinity as any, w: 6, h: 6, type: 'chart', config: { title: 'New Panel' } }
    setPanels(prev=>[...prev, p])
    setLayout(prev=>[...prev, { i: p.i, x: p.x, y: p.y, w: p.w, h: p.h }])
  }

  function removePanel(id:string){
    setPanels(prev=>prev.filter(p=>p.i!==id))
    setLayout(prev=>prev.filter(l=>l.i!==id))
  }

  function openConfig(panel:any){
    setConfigPanel(panel)
  }

  function saveConfig(panel:any){
    // Save panel configuration (e.g., SQL) and try to fetch runtimeData via queryPreview
    console.log('saveConfig called', { panel, setPanelsType: typeof setPanels })
    try{
      if(typeof setPanels !== 'function'){
        console.error('saveConfig: setPanels is not a function', setPanels)
      }else{
        setPanels(prev=>{
          console.log('saveConfig prev panels', prev)
          try{
            const arr = Array.isArray(prev) ? prev : []
            const next = arr.map(p=> p.i === panel.i ? panel : p)
            console.log('saveConfig next panels', next)
            return next
          }catch(err){ console.error('error mapping panels', err); return prev }
        })
      }
    }catch(err){ console.error('saveConfig initial setPanels error', err) }
  // Fetch SQL preview for the panel and write the result to panel.runtimeData for immediate preview
    (async ()=>{
      try{
  // If panel.config contains SQL and a datasource, run SQL preview first
        const sql = panel.config?.sql
        const ds = panel.config?.datasource || panel.config?.datasourceId || panel.config?.dataset
        if(sql && ds){
          try{
              const tr = computeTimeRangeFromSelector()
              const time_range = tr ? { from: tr[0], to: tr[1] } : undefined
              console.debug('saveConfig preview sending time_range', time_range, 'time_field', timestampField, 'panel', panel.i)
              const previewRes = await queryPreview({ datasource: ds, sql: sql, limit: 200, time_range, time_field: timestampField })
            const cols = (previewRes.columns || []).map((c:any)=> typeof c === 'string' ? c : c.name)
            const rows = previewRes.rows || []
            const mapped = rows.map((rrow:any[])=>{
              const obj:any = {}
              cols.forEach((c:string|any,i:number)=>{
                const name = typeof c === 'string' ? c : c.name
                obj[name] = rrow[i]
              })
              return obj
            })
            try{
            // Write runtimeData back to state for immediate preview
              if(typeof setPanels === 'function') setPanels(prev=> (Array.isArray(prev) ? prev.map(p=> p.i === panel.i ? { ...p, runtimeData: mapped } : p) : prev))
              else console.error('setPanels not a function when attaching runtimeData', setPanels)
            }catch(err){ console.error('error setting runtimeData panels', err) }
            return
          }catch(e:any){ console.warn('sql preview failed', e); try{ console.error('preview error response', e?.response?.data) }catch(err){} }
        }
        // dataset fallback removed — SQL-only flow above handles runtimeData
      }catch(e){ console.warn('panel preview failed', e) }
    })()
    setConfigPanel(null)
  }

  // When the dashboard-level timestamp field or time selector changes, refresh all panels
  useEffect(()=>{
    // avoid running when no panels
    if(!panels || panels.length === 0) return
    // compute current time range and refresh using it
    (async ()=>{
      try{
        await refreshPanelsFor(panels)
      }catch(e){ console.warn('refresh on time selector change failed', e) }
    })()
  },[timestampField, timeSelector, timestampFrom, timestampTo, timestampRelativeCustomValue, timestampRelativeCustomUnit, timestampRelative])

  async function handleSave(){
    const payload: any = { name: name || 'Unnamed', description: description || '', layout: panels }
    if(timestampField) payload.timestamp_field = timestampField
    if(timeSelector) payload.time_selector = timeSelector
    if(timestampRelative) payload.timestamp_relative = timestampRelative
    if(timestampRelativeCustomValue) payload.timestamp_relative_custom_value = timestampRelativeCustomValue
    if(timestampRelativeCustomUnit) payload.timestamp_relative_custom_unit = timestampRelativeCustomUnit
    if(timestampFrom) payload.timestamp_from = typeof timestampFrom.toISOString === 'function' ? timestampFrom.toISOString() : String(timestampFrom)
    if(timestampTo) payload.timestamp_to = typeof timestampTo.toISOString === 'function' ? timestampTo.toISOString() : String(timestampTo)
    try{
      if(dashboardId){
        await updateDashboard(dashboardId, payload)
        Modal.success({ title: 'Saved' })
      }else{
        const created = await createDashboard(payload)
        Modal.success({ title: 'Created', content: `ID ${created.id}` })
      }
    }catch(e){
      Modal.error({ title: 'Save failed', content: String(e) })
    }
  }

  // dataset support removed — runtimeData is populated from SQL preview when saving configs

  // helper to measure text width using canvas
  function measureTextWidth(text: string, font = '12px sans-serif'){
    try{
      const canvas = document.createElement('canvas')
      const ctx = canvas.getContext('2d')
      if(!ctx) return text.length * 7
      ctx.font = font
      const metrics = ctx.measureText(text)
      return Math.ceil(metrics.width)
    }catch(e){
      return text.length * 7
    }
  }

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          {/* Inline dashboard name editing when in edit mode */}
          {isEditMode ? (
            <input value={name} onChange={e=>setName(e.target.value)} onBlur={handleSave} onKeyDown={e=>{ if(e.key === 'Enter') handleSave() }} placeholder="Dashboard name" style={{ padding: 8, borderRadius: 6, border: '1px solid #ddd' }} />
          ) : (
            <div style={{ fontWeight: 700 }}>{name || 'Unnamed Dashboard'}</div>
          )}
          <input value={description} onChange={e=>setDescription(e.target.value)} placeholder="Description" style={{ padding: 8, borderRadius: 6, border: '1px solid #ddd', width: 360 }} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input placeholder="Timestamp field (optional)" value={timestampField ?? ''} onChange={e=>setTimestampField(e.target.value || null)} style={{ width: 200 }} />
            <Select style={{ width: 160 }} allowClear placeholder="Time range" value={timeSelector ?? undefined} onChange={(val:any)=>{
              setTimeSelector(val || null)
              // keep legacy fields in sync
              if(val === 'absolute'){
                setTimestampRelative(null)
                setTimestampFrom(null)
                setTimestampTo(null)
              } else if(val === 'custom_relative'){
                setTimestampRelative('custom')
              } else {
                setTimestampRelative(val)
              }
            }}>
              <Select.Option value="1h">Last 1 hour</Select.Option>
              <Select.Option value="6h">Last 6 hours</Select.Option>
              <Select.Option value="24h">Last 24 hours</Select.Option>
              <Select.Option value="7d">Last 7 days</Select.Option>
              <Select.Option value="custom_relative">Custom relative</Select.Option>
              <Select.Option value="absolute">Absolute (pick timestamps)</Select.Option>
            </Select>
          </div>
        </div>
        <Space>
          <Button onClick={()=>setIsEditMode(m=>!m)}>{isEditMode ? 'Exit Edit' : 'Enter Edit'}</Button>
          <Button onClick={addPanel} disabled={!isEditMode}>Add Panel</Button>
          <Button onClick={()=>{ setSqlDatasource(null); setSqlText(''); setSqlModalVisible(true) }} disabled={!isEditMode}>New Panel from SQL</Button>
          <Button type="primary" onClick={handleSave}>Save Layout</Button>
          <Button onClick={onBack}>Back to List</Button>
        </Space>
      </div>
      {/* Render absolute/custom controls */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        {timeSelector === 'absolute' ? (
          <>
            <DatePicker showTime value={timestampFrom ?? undefined} onChange={(d:any)=>setTimestampFrom(d ?? null)} />
            <DatePicker showTime value={timestampTo ?? undefined} onChange={(d:any)=>setTimestampTo(d ?? null)} />
          </>
        ) : null}
        {timeSelector === 'custom_relative' ? (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <InputNumber min={1} value={timestampRelativeCustomValue ?? undefined} onChange={(v:any)=>setTimestampRelativeCustomValue(v ?? null)} />
            <Select value={timestampRelativeCustomUnit ?? undefined} onChange={(v:any)=>setTimestampRelativeCustomUnit(v ?? null)} style={{ width: 120 }}>
              <Select.Option value="m">minutes</Select.Option>
              <Select.Option value="h">hours</Select.Option>
              <Select.Option value="d">days</Select.Option>
            </Select>
          </div>
        ) : null}
      </div>

      <div style={{ border: '1px dashed #ddd', padding: 0, borderRadius: 8, width: '100%' }}>
  <GridLayout className="layout" layout={layout} cols={12} rowHeight={30} draggableHandle=".panel-header" draggableCancel=".no-drag, button, input, .ant-btn" onLayoutChange={onLayoutChange} margin={[0,0]} containerPadding={[0,0]} width={undefined as any} isDraggable={isEditMode} isResizable={isEditMode}>
          {panels.map(p=> (
            <div key={p.i} data-grid={{ i: p.i, x: p.x, y: p.y, w: p.w, h: p.h }} style={{ background: '#fff', borderRadius: 8, boxShadow: '0 1px 4px rgba(0,0,0,0.08)', overflow: 'hidden' }}>
              <Panel panel={p} onConfigure={openConfig} onRemove={removePanel} isEditMode={isEditMode} onTitleChange={(newTitle:string)=>{
                setPanels(prev=> prev.map(pp=> pp.i === p.i ? { ...pp, config: { ...pp.config, title: newTitle } } : pp))
              }}>
                {(size:{width:number,height:number}) => (
                  <div style={{ width: '100%', height: '100%' }}>
                    {p.type === 'chart' || (p.type && p.type !== 'table' && p.type !== 'text' && p.type !== 'image') ? (
                          (() => {
                            const raw = (p.runtimeData && p.runtimeData.length>0) ? p.runtimeData : []
                            const data = Array.isArray(raw) ? raw : (raw ? [raw] : [])
                            const bindings = p.config?.fieldBindings || {}
                            const chartType = p.config?.chartType || p.type || 'column'
                            // attempt to detect default x field if not bound
                            const detectedX = data && data[0] ? Object.keys(data[0]).find(k=>k!=='value' && k!=='count' && k!=='amount') || Object.keys(data[0])[0] : 'name'
                            try{
                              switch(chartType){
                                case 'line':
                                  return <Line data={data} xField={bindings.xField || detectedX} yField={bindings.yField || 'value'} height={size.height} width={size.width} />
                                case 'pie':
                                  return <Pie data={data} angleField={bindings.angleField || 'value'} colorField={bindings.colorField || detectedX} height={size.height} width={size.width} />
                                case 'scatter':
                                  return <Scatter data={data} xField={bindings.xField || 'x'} yField={bindings.yField || 'y'} height={size.height} width={size.width} />
                                case 'bar': {
                                  const barX = bindings.xField || 'value'
                                  const barY = bindings.yField || detectedX
                                  return <Bar data={data} xField={barX} yField={barY} height={size.height} width={size.width} />
                                }
                                case 'column':
                                default:
                                  return <Column data={data} xField={bindings.xField || detectedX} yField={bindings.yField || 'value'} height={size.height} width={size.width} />
                              }
                            }catch(e){
                              // fallback to basic column
                              const fallbackX = Object.keys(data[0]||{}).find(k=>k!=='value') || 'name'
                              return <Column data={data} xField={fallbackX} yField="value" height={size.height} width={size.width} />
                            }
                          })()
                        ) : p.type === 'table' ? (
                      <>
                        {p.runtimeData && p.runtimeData.length>0 ? (
                          <Table dataSource={p.runtimeData} columns={Object.keys(p.runtimeData[0]).map(k=>({ title: k, dataIndex: k, key: k }))} pagination={false} rowKey={(r:any)=> JSON.stringify(r)} />
                        ) : (
                          <div>No data</div>
                        )}
                      </>
                    ) : (
                      <div>Preview</div>
                    )}
                  </div>
                )}
              </Panel>
            </div>
          ))}
        </GridLayout>
      </div>

      {
        (()=>{
          const tr = computeTimeRangeFromSelector()
          const asDates = tr ? ([dayjs(tr[0]), dayjs(tr[1])] as [any, any]) : null
          return <PanelConfigModal visible={!!configPanel} panel={configPanel} onCancel={()=>setConfigPanel(null)} onSave={saveConfig} dashboardTimeRange={asDates} dashboardTimestampField={timestampField} />
        })()
      }
      <Modal
        title="Create Panel from SQL"
        open={sqlModalVisible}
        onCancel={()=>setSqlModalVisible(false)}
        onOk={async ()=>{
          console.log('SQL modal OK clicked', { sqlDatasource, sqlText })
          if(!sqlDatasource || !sqlText){ message.error('Select a data source and enter SQL'); return }
          message.loading({ content: 'Running SQL preview...', key: 'sqlPreview' })
          try{
            const tr = computeTimeRangeFromSelector()
            const time_range = tr ? { from: tr[0], to: tr[1] } : undefined
            console.debug('SQL modal preview sending time_range', time_range, 'time_field', timestampField)
            const res = await queryPreview({ datasource: sqlDatasource, sql: sqlText, limit: 200, time_range, time_field: timestampField })
            console.log('queryPreview result', res)
            const cols = res.columns || []
            const rows = res.rows || []
            const mapped = rows.map((r:any[])=>{
              const obj:any = {}
              cols.forEach((c:any,i:number)=>{
                const name = typeof c === 'string' ? c : c.name
                obj[name] = r[i]
              })
              return obj
            })
            // create panel in current dashboard
            const id = String(Date.now())
            const newPanel = { i: id, x: 0, y: Infinity as any, w: 6, h: 6, type: 'table', config: { title: 'SQL Panel', datasource: sqlDatasource, sql: sqlText }, runtimeData: mapped }
            setPanels(prev=>{
              const next = [...prev, newPanel]
              console.log('panels after add', next)
              return next
            })
            setLayout(prev=>{
              const next = [...prev, { i: id, x: 0, y: Infinity as any, w: 6, h: 6 }]
              console.log('layout after add', next)
              return next
            })
            setSqlModalVisible(false)
            // clear SQL inputs so subsequent opens start fresh
            setSqlDatasource(null)
            setSqlText('')
            message.success({ content: 'Panel created from SQL', key: 'sqlPreview' })
          }catch(e:any){
            console.error('SQL preview failed', e)
            try{ console.error('SQL modal preview response', e?.response?.data) }catch(err){}
            message.error({ content: `SQL preview failed: ${e?.message || e}`, key: 'sqlPreview' })
          }
        }}
      >
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <Select style={{ minWidth: 240 }} allowClear placeholder="Select data source" value={sqlDatasource ?? undefined} onChange={v=>setSqlDatasource(v)}>
            {datasources.map((d:any)=> <Select.Option key={d.id} value={d.id}>{d.name}</Select.Option>)}
          </Select>
        </div>
        <div>
          <textarea rows={8} style={{ width:'100%', fontFamily: 'monospace' }} value={sqlText} onChange={e=>setSqlText(e.target.value)} placeholder="Enter SQL here" />
        </div>
      </Modal>
    </div>
  )
}
