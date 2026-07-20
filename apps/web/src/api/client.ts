import type { ApiEnvelope } from '../types/contracts'

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/events'

export class ApiError extends Error { constructor(public code:string, message:string, public status:number){super(message)} }

export async function request<T>(path:string, init?:RequestInit):Promise<T>{
  const response = await fetch(`${API_BASE}${path}`, {headers:{'Content-Type':'application/json'}, ...init})
  const body = await response.json() as ApiEnvelope<T> | {error:{code:string;message:string}}
  if(!response.ok){const error='error' in body?body.error:{code:'HTTP_ERROR',message:response.statusText};throw new ApiError(error.code,error.message,response.status)}
  return (body as ApiEnvelope<T>).data
}

export const post = <T>(path:string, body?:unknown) => request<T>(path,{method:'POST',body:body === undefined ? undefined : JSON.stringify(body)})
