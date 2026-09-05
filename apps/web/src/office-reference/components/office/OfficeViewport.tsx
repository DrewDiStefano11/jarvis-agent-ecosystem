import { PointerEvent as ReactPointerEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { OFFICE_ASSETS } from '../../office/assets';
import { constrainTransform, fitTransform, screenToOffice, zoomAtScreenPoint } from '../../office/coordinates';
import { DEFAULT_VIEWPORT_OPTIONS, OFFICE_SOURCE_HEIGHT, OFFICE_SOURCE_WIDTH } from '../../office/constants';
import {
    beginPanGesture,
    beginPinchGesture,
    PanGesture,
    PinchGesture,
    resumePanGesture,
    shouldSuppressSelection,
    updatePanGesture,
    updatePinchGesture,
} from '../../office/gestures';
import { panTransform, resolveFocusRequest } from '../../office/interaction';
import { LAYER_ORDER } from '../../office/layers';
import { OfficeLayer, OfficeOverlayDocument, Point, ViewTransform, ViewportSize } from '../../office/types';
import { OverlayRenderer } from './OverlayRenderer';

type Props = Readonly<{
    active: boolean;
    presentation?: 'inspection' | 'simulation';
    document: OfficeOverlayDocument;
    debug: boolean;
    reviewMode?: boolean;
    selectedId: string | null;
    hoveredId: string | null;
    visibleLayers: ReadonlySet<OfficeLayer>;
    transformTelemetry?: ViewTransform;
    pointerTelemetry?: Point | null;
    onSetVisibleLayers?: (layers: ReadonlySet<OfficeLayer>) => void;
    onSelect: (id: string | null) => void;
    onHover: (id: string | null) => void;
    onPointerOfficePoint: (point: Point | null) => void;
    onTransformChange: (transform: ViewTransform) => void;
    focusRequest: number;
}>;

export function OfficeViewport({
    active,
    presentation = 'inspection',
    document,
    debug,
    reviewMode = false,
    selectedId,
    hoveredId,
    visibleLayers,
    transformTelemetry,
    pointerTelemetry = null,
    onSetVisibleLayers = () => undefined,
    onSelect,
    onHover,
    onPointerOfficePoint,
    onTransformChange,
    focusRequest,
}: Props) {
    const viewportRef = useRef<HTMLDivElement>(null);
    const transformRef = useRef<ViewTransform>({ scale: 0.1, x: 0, y: 0 });
    const panRef = useRef<PanGesture | null>(null);
    const touchesRef = useRef(new Map<number, Point>());
    const pinchRef = useRef<PinchGesture | null>(null);
    const suppressClickRef = useRef(false);
    const frameRef = useRef<number | null>(null);
    const pointerFrameRef = useRef<number | null>(null);
    const pendingTransformRef = useRef<ViewTransform | null>(null);
    const lastHandledFocusRequestRef = useRef(0);
    const [transform, setTransform] = useState<ViewTransform>({ scale: 0.1, x: 0, y: 0 });
    const [viewport, setViewport] = useState<ViewportSize>({ width: 1, height: 1 });
    const [backgroundState, setBackgroundState] = useState<'loading' | 'ready' | 'error'>('loading');
    const [reducedMotion, setReducedMotion] = useState(
        () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    );
    const minimumZoom = Math.min(
        DEFAULT_VIEWPORT_OPTIONS.minimumZoom,
        fitTransform(viewport).scale,
    );

    const commitTransform = useCallback((next: ViewTransform) => {
        const constrained = constrainTransform(next, viewport, OFFICE_SOURCE_WIDTH, OFFICE_SOURCE_HEIGHT, DEFAULT_VIEWPORT_OPTIONS.boundaryPadding);
        transformRef.current = constrained;
        setTransform(constrained);
        onTransformChange(constrained);
    }, [onTransformChange, viewport]);

    const fit = useCallback(() => {
        if (viewport.width <= 1 || viewport.height <= 1) return;
        commitTransform(fitTransform(viewport));
    }, [commitTransform, viewport]);

    useLayoutEffect(() => {
        const element = viewportRef.current;
        if (!element) return;
        let firstMeasurement = true;
        const observer = new ResizeObserver(entries => {
            const rect = entries[0]?.contentRect;
            if (!rect) return;
            if (!Number.isFinite(rect.width) || !Number.isFinite(rect.height) || rect.width <= 0 || rect.height <= 0) return;
            const nextViewport = { width: rect.width, height: rect.height };
            setViewport(nextViewport);
            if (firstMeasurement) {
                const next = fitTransform(nextViewport);
                transformRef.current = next;
                setTransform(next);
                onTransformChange(next);
                firstMeasurement = false;
            } else {
                const next = reviewMode
                    ? fitTransform(nextViewport)
                    : constrainTransform(transformRef.current, nextViewport, OFFICE_SOURCE_WIDTH, OFFICE_SOURCE_HEIGHT, DEFAULT_VIEWPORT_OPTIONS.boundaryPadding);
                transformRef.current = next;
                setTransform(next);
                onTransformChange(next);
            }
        });
        observer.observe(element);
        return () => observer.disconnect();
    }, [onTransformChange, reviewMode]);

    useEffect(() => () => {
        if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
        if (pointerFrameRef.current !== null) cancelAnimationFrame(pointerFrameRef.current);
    }, []);

    useEffect(() => {
        const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
        const handleChange = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
        preference.addEventListener('change', handleChange);
        return () => preference.removeEventListener('change', handleChange);
    }, []);

    useEffect(() => {
        if (!active) return;
        const handleKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onSelect(null);
        };
        window.addEventListener('keydown', handleKey);
        return () => window.removeEventListener('keydown', handleKey);
    }, [active, onSelect]);

    useEffect(() => {
        const element = viewportRef.current;
        if (!element) return;
        const handleWheel = (event: globalThis.WheelEvent) => {
            event.preventDefault();
            const rect = element.getBoundingClientRect();
            const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
            const multiplier = Math.exp(-event.deltaY * 0.0015);
            const nextScale = Math.min(DEFAULT_VIEWPORT_OPTIONS.maximumZoom, Math.max(minimumZoom, transformRef.current.scale * multiplier));
            commitTransform(zoomAtScreenPoint(transformRef.current, point, nextScale));
        };
        element.addEventListener('wheel', handleWheel, { passive: false });
        return () => element.removeEventListener('wheel', handleWheel);
    }, [commitTransform, minimumZoom]);

    useEffect(() => {
        const entity = selectedId ? document.entities.find(item => item.id === selectedId) : undefined;
        const resolution = resolveFocusRequest(
            focusRequest,
            lastHandledFocusRequestRef.current,
            entity,
            transformRef.current,
            viewport,
            DEFAULT_VIEWPORT_OPTIONS.maximumZoom,
        );
        if (!resolution) return;
        commitTransform(resolution.transform);
        lastHandledFocusRequestRef.current = resolution.request;
    }, [commitTransform, document.entities, focusRequest, selectedId, viewport]);

    const localPoint = (clientX: number, clientY: number): Point => {
        const rect = viewportRef.current?.getBoundingClientRect();
        return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
    };

    const capturePointer = (pointerId: number) => {
        const element = viewportRef.current;
        if (!element || element.hasPointerCapture(pointerId)) return;
        element.setPointerCapture(pointerId);
    };

    const releaseCapturedPointer = (pointerId: number) => {
        const element = viewportRef.current;
        if (!element?.hasPointerCapture(pointerId)) return;
        element.releasePointerCapture(pointerId);
    };

    const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0 && event.pointerType === 'mouse') return;
        const point = localPoint(event.clientX, event.clientY);
        touchesRef.current.set(event.pointerId, point);
        if (touchesRef.current.size === 1) {
            suppressClickRef.current = false;
            panRef.current = beginPanGesture(event.pointerId, point);
        } else if (touchesRef.current.size === 2) {
            const [a, b] = [...touchesRef.current.values()];
            if (!a || !b) return;
            const initialTransform = pendingTransformRef.current ?? transformRef.current;
            if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
            frameRef.current = null;
            pendingTransformRef.current = null;
            commitTransform(initialTransform);
            pinchRef.current = beginPinchGesture(a, b, initialTransform);
            panRef.current = null;
            suppressClickRef.current = true;
            touchesRef.current.forEach((_point, pointerId) => capturePointer(pointerId));
        }
    };

    const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
        const local = localPoint(event.clientX, event.clientY);
        if (debug) {
            if (pointerFrameRef.current !== null) cancelAnimationFrame(pointerFrameRef.current);
            pointerFrameRef.current = requestAnimationFrame(() => onPointerOfficePoint(screenToOffice(local, transformRef.current)));
        }
        if (!touchesRef.current.has(event.pointerId)) return;
        touchesRef.current.set(event.pointerId, local);
        if (touchesRef.current.size === 2 && pinchRef.current) {
            const [a, b] = [...touchesRef.current.values()];
            if (!a || !b) return;
            commitTransform(updatePinchGesture(
                pinchRef.current,
                a,
                b,
                minimumZoom,
                DEFAULT_VIEWPORT_OPTIONS.maximumZoom,
            ));
            return;
        }
        const pan = panRef.current;
        if (!pan || pan.pointerId !== event.pointerId) return;
        const updated = updatePanGesture(pan, local);
        panRef.current = updated.gesture;
        if (shouldSuppressSelection(updated.gesture)) {
            suppressClickRef.current = true;
            capturePointer(event.pointerId);
        }
        const base = pendingTransformRef.current ?? transformRef.current;
        pendingTransformRef.current = panTransform(base, updated.delta.x, updated.delta.y, viewport, OFFICE_SOURCE_WIDTH, OFFICE_SOURCE_HEIGHT, DEFAULT_VIEWPORT_OPTIONS.boundaryPadding);
        if (frameRef.current === null) {
            frameRef.current = requestAnimationFrame(() => {
                const next = pendingTransformRef.current;
                pendingTransformRef.current = null;
                frameRef.current = null;
                if (next) commitTransform(next);
            });
        }
    };

    const finishPointer = (pointerId: number, releaseCapture: boolean) => {
        if (!touchesRef.current.has(pointerId)) return;
        const hadPinch = pinchRef.current !== null;
        if (panRef.current?.pointerId === pointerId && shouldSuppressSelection(panRef.current)) {
            suppressClickRef.current = true;
        }
        touchesRef.current.delete(pointerId);
        if (hadPinch && touchesRef.current.size === 1) {
            panRef.current = resumePanGesture(touchesRef.current);
        } else if (panRef.current?.pointerId === pointerId) {
            panRef.current = null;
        }
        if (touchesRef.current.size < 2) pinchRef.current = null;
        if (releaseCapture) releaseCapturedPointer(pointerId);
    };

    const releasePointer = (event: ReactPointerEvent<HTMLDivElement>) => {
        finishPointer(event.pointerId, true);
    };

    const handleLostPointerCapture = (event: ReactPointerEvent<HTMLDivElement>) => {
        finishPointer(event.pointerId, false);
    };

    const zoomBy = (factor: number) => {
        const center = { x: viewport.width / 2, y: viewport.height / 2 };
        const nextScale = Math.min(DEFAULT_VIEWPORT_OPTIONS.maximumZoom, Math.max(minimumZoom, transform.scale * factor));
        commitTransform(zoomAtScreenPoint(transform, center, nextScale));
    };

    return (
        <div className={`office-viewport-shell ${reviewMode ? 'office-viewport-shell--candidate' : ''} office-viewport-shell--${presentation}`}>
            <div className="viewport-controls" aria-label="Viewport controls">
                <button type="button" onClick={() => zoomBy(DEFAULT_VIEWPORT_OPTIONS.zoomStep)} aria-label="Zoom in">+</button>
                <button type="button" onClick={() => zoomBy(1 / DEFAULT_VIEWPORT_OPTIONS.zoomStep)} aria-label="Zoom out">−</button>
                <button type="button" onClick={fit}>Fit office</button>
                <button type="button" onClick={fit}>Reset view</button>
            </div>
            <div
                ref={viewportRef}
                className="office-viewport"
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={releasePointer}
                onPointerCancel={releasePointer}
                onLostPointerCapture={handleLostPointerCapture}
                onClickCapture={event => {
                    if (!suppressClickRef.current) return;
                    event.preventDefault();
                    event.stopPropagation();
                    suppressClickRef.current = false;
                }}
                onPointerLeave={event => {
                    if (pointerFrameRef.current !== null) cancelAnimationFrame(pointerFrameRef.current);
                    pointerFrameRef.current = null;
                    if (debug) onPointerOfficePoint(null);
                    if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
                        finishPointer(event.pointerId, false);
                    }
                }}
                onDoubleClick={fit}
                onClick={event => {
                    if (event.target === event.currentTarget || (event.target as HTMLElement).classList.contains('office-surface')) onSelect(null);
                }}
            >
                <div
                    className="office-surface"
                    style={{ transform: `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})` }}
                >
                    {backgroundState !== 'error' && (
                        <img
                            className="office-background"
                            src={OFFICE_ASSETS.background.path}
                            alt="Jarvis office"
                            draggable={false}
                            onLoad={event => {
                                const image = event.currentTarget;
                                setBackgroundState(image.naturalWidth === OFFICE_SOURCE_WIDTH && image.naturalHeight === OFFICE_SOURCE_HEIGHT ? 'ready' : 'error');
                            }}
                            onError={() => setBackgroundState('error')}
                        />
                    )}
                    {backgroundState !== 'ready' && <div className="office-background-fallback" aria-hidden="true" />}
                    <OverlayRenderer
                        entities={document.entities}
                        visibleLayers={visibleLayers}
                        debug={debug}
                        reviewMode={reviewMode}
                        selectedId={selectedId}
                        hoveredId={hoveredId}
                        showLabels={transform.scale >= (reviewMode ? 0.08 : 0.15)}
                        reducedMotion={reducedMotion}
                        onHover={onHover}
                        onSelect={onSelect}
                    />
                </div>
                {backgroundState === 'loading' && <div className="asset-status" role="status">Loading 8K office image…</div>}
                {backgroundState === 'error' && (
                    <div className="asset-status asset-status--error" role="alert">
                        Required 8192×5460 background missing or dimensionally invalid: <code>{OFFICE_ASSETS.background.path}</code>
                    </div>
                )}
                {debug && <div className="debug-layer-order">Layers: {LAYER_ORDER.join(' → ')}</div>}
            </div>
        </div>
    );
}
