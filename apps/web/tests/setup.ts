import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
afterEach(()=>cleanup())
Object.defineProperty(window,'matchMedia',{writable:true,value:(query:string)=>({matches:false,media:query,onchange:null,addListener:()=>undefined,removeListener:()=>undefined,addEventListener:()=>undefined,removeEventListener:()=>undefined,dispatchEvent:()=>false})})

// jsdom has no layout engine; browser smoke tests verify the real observer.
class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, "ResizeObserver", { writable: true, value: TestResizeObserver })
