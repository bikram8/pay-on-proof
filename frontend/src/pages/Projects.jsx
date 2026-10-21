import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AgGridReact } from 'ag-grid-react'
import { api, money } from '../api.js'
import { Empty, ErrorBox, Status, formatTime, useGridTheme } from '../components.jsx'

export default function Projects() {
  const navigate = useNavigate()
  const theme = useGridTheme()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => { api('/projects').then(setRows).catch(setError) }, [])

  const columns = useMemo(() => [
    { field: 'id', headerName: '#', width: 70 },
    { field: 'title', flex: 2, minWidth: 180 },
    { field: 'status', cellRenderer: p => <Status value={p.value} />, width: 150 },
    {
      headerName: 'Progress', width: 120,
      valueGetter: p => `${p.data.milestones.filter(m => m.status === 'PAID_OUT').length} / ${p.data.milestones.length}`,
    },
    { field: 'total_amount', headerName: 'Total', width: 120, valueFormatter: p => money(p.value, p.data.currency) },
    { field: 'paid_out', headerName: 'Paid out', width: 120, valueFormatter: p => money(p.value, p.data.currency) },
    { field: 'escrow_mode', headerName: 'Mode', width: 150, valueFormatter: p => p.value === 'AUTH_CAPTURE' ? 'Hold & capture' : 'Escrow & payout' },
    { field: 'created_at', headerName: 'Created', width: 170, valueFormatter: p => formatTime(p.value), sort: 'desc' },
  ], [])

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="muted">Each project holds the funder's money until milestones are verified.</p>
        </div>
        <Link to="/projects/new" className="button primary">New project</Link>
      </div>
      <ErrorBox error={error} />
      {rows && rows.length === 0 ? (
        <Empty title="No projects yet">Create one to set milestones, fund it with PayPal and share the worker link.</Empty>
      ) : (
        <div className={`grid ${theme}`}>
          <AgGridReact
            rowData={rows}
            columnDefs={columns}
            defaultColDef={{ sortable: true, resizable: true }}
            onRowClicked={e => navigate(`/projects/${e.data.id}`)}
            rowClass="clickable"
            domLayout="autoHeight"
          />
        </div>
      )}
    </section>
  )
}
