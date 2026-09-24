import type { ChartMarker, ChartMarkerEvent } from "./reportCharts";

interface MarkerDetailsPanelProps {
  marker: ChartMarker;
  onClose: () => void;
}

function eventLabel(event: ChartMarkerEvent): string {
  return event.markerType.replaceAll("_", " ");
}

export function MarkerDetailsPanel({ marker, onClose }: MarkerDetailsPanelProps) {
  const events = marker.events ?? [];
  const heading = marker.kind === "group" ? `${marker.markerCount ?? events.length} Events` : marker.label;
  return <section className="marker-details-panel" role="dialog" aria-modal="false" aria-label={`${heading} details`}>
    <header>
      <div><span className="eyebrow">EVENT DETAILS</span><h4>{heading}</h4><small>{marker.date}</small></div>
      <button className="button button-quiet marker-details-close" type="button" onClick={onClose} aria-label="Close event details">Close</button>
    </header>
    <div className="marker-event-list">
      {events.map((event) => <article className="marker-event-detail" data-marker-id={event.markerId} key={event.markerId}>
        <div className="marker-event-heading"><strong>{eventLabel(event)}</strong><code>{event.markerId}</code></div>
        <h5>{event.title}</h5>
        <p>{event.summary}</p>
        <ul>{event.details.map((detail, index) => <li key={`${event.markerId}-${index}`}>{detail}</li>)}</ul>
        <small>Source: {event.sourceEventType} · {event.sourceEventReference}</small>
      </article>)}
      {events.length === 0 && <article className="marker-event-detail"><h5>{marker.label}</h5><ul>{marker.details.map((detail, index) => <li key={`${detail}-${index}`}>{detail}</li>)}</ul></article>}
    </div>
  </section>;
}
