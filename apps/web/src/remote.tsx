import React from 'react'
import ReactDOM from 'react-dom/client'
import { RemoteOperatorProvider } from './state/RemoteOperatorStore'
import { RemoteOperation } from './pages/RemoteOperation'
import './styles/remote.css'
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><RemoteOperatorProvider><RemoteOperation/></RemoteOperatorProvider></React.StrictMode>)
