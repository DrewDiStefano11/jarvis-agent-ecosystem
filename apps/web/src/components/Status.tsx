export function Status({value}:{value:string}){return <span className={`status status-${value}`}><span aria-hidden="true">●</span> {value.replaceAll('_',' ')}</span>}
export function Progress({value,label='Progress'}:{value:number;label?:string}){return <div className="progress-wrap"><div className="progress-label"><span>{label}</span><span>{value}%</span></div><progress max="100" value={value}>{value}%</progress></div>}
export function Empty({children}:{children:string}){return <div className="empty">{children}</div>}
