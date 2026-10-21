import { useEffect, useMemo, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { api } from '../api.js'
import { ErrorBox, Status, formatTime, useGridTheme } from '../components.jsx'

const MONEY_ACTIONS = new Set(['AUTHORIZE', 'CAPTURE_TO_ESCROW', 'CAPTURE', 'PAYOUT', 'PAYOUT_STATUS', 'VOID', 'REFUND'])

function summarize(detail = {}) {
  const parts = []
  if (detail.status) parts.push(detail.previous ? `${detail.previous} → ${detail.status}` : detail.status)
  if (detail.amount != null) parts.push(`$${Number(detail.amount).toFixed(2)}`)
  if (detail.receiver) parts.push(`to ${detail.receiver}`)
  if (detail.confidence != null) parts.push(`confidence ${Math.round(detail.confidence * 100)}%`)
  if (detail.hard_flags?.length) parts.push(`flags: ${detail.hard_flags.join(', ')}`)
  if (detail.reason) parts.push(detail.reason)
  if (detail.note) parts.push(`note: ${detail.note}`)
  if (detail.status_code) parts.push(`PayPal ${detail.status_code}`)
  const ids = ['capture_id', 'batch_id', 'authorization_id', 'order_id'].filter(k => detail[k]).map(k => detail[k])
  if (ids.length) parts.push(ids.join(' '))
  return parts.join(' · ')
}

export default function Activity() {
  const theme = useGridTheme()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [onlyMoney, setOnlyMoney] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => { api('/audit').then(setRows).catch(setError) }, [])

  const shown = useMemo(() => (rows || []).filter(r => !onlyMoney || MONEY_ACTIONS.has(r.action)), [rows, onlyMoney])

  const columns = useMemo(() => [
    { field: 'at', headerName: 'Time', width: 170, valueFormatter: p => formatTime(p.value), sort: 'desc' },
    { field: 'project_id', headerName: 'Project', width: 100 },
    { field: 'milestone_id', headerName: 'Milestone', width: 110 },
    { field: 'actor', width: 130 },
    { field: 'action', width: 170 },
    { field: 'outcome', width: 130, cellRenderer: p => <Status value={p.value} /> },
    { headerName: 'Detail', flex: 1, minWidth: 260, valueGetter: p => summarize(p.data.detail), tooltipValueGetter: p => JSON.stringify(p.data.detail, null, 1) },
  ], [])

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <p className="muted">Every AI decision, policy block and PayPal money movement, newest first.</p>
        </div>
        <div className="head-actions">
          <input type="search" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} />
          <label className="toggle">
            <input type="checkbox" checked={onlyMoney} onChange={e => setOnlyMoney(e.target.checked)} />
            Money movements only
          </label>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className={`grid tall ${theme}`}>
        <AgGridReact
          rowData={shown}
          columnDefs={columns}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          quickFilterText={search}
          pagination
          paginationPageSize={50}
          tooltipShowDelay={300}
        />
      </div>
    </section>
  )
}
