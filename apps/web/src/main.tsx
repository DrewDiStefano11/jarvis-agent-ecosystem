import React from 'react';import ReactDOM from 'react-dom/client';import { BrowserRouter } from 'react-router-dom';import App from './App';import { AppStoreProvider } from './state/AppStore';import './styles/app.css'
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><BrowserRouter basename={import.meta.env.BASE_URL}><AppStoreProvider><App/></AppStoreProvider></BrowserRouter></React.StrictMode>)
if('serviceWorker' in navigator&&import.meta.env.PROD)window.addEventListener('load',()=>void navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`))
