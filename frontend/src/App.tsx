import { useEffect, useMemo, useState } from "react";

type DocumentMetadata = {
  documentName: string;
  fileType: string;
  contentOwner: string;
  summary: string;
  topics: string[];
  businessArea: string;
  audience: string;
  documentRisk: string;
  lastReviewedDate: string;
  freshnessStatus: string;
  containsPricing: boolean;
  containsConfidentialInfo: boolean;
  suggestedTags: string[];
  reviewStatus: string;
  humanReviewRequired: boolean;
  extractedCharacters: number;
};

function App() {
  const [documents, setDocuments] = useState<DocumentMetadata[]>([]);
  const [selected, setSelected] = useState<DocumentMetadata | null>(null);

  useEffect(() => {
    fetch("/api/documents").then((res) => res.json()).then((data) => {
      setDocuments(data);
      setSelected(data[0] ?? null);
    });
  }, []);

  const stats = useMemo(() => {
    const total = documents.length || 1;
    const current = documents.filter((d) => d.freshnessStatus === "Current").length;
    const reviewed = documents.filter((d) => d.reviewStatus === "Approved for AI" || d.reviewStatus === "Human Review Required").length;
    const owned = documents.filter((d) => d.contentOwner !== "Unassigned").length;
    return {
      total: documents.length,
      current,
      needsReview: documents.filter((d) => d.freshnessStatus === "Needs Review").length,
      stale: documents.filter((d) => d.freshnessStatus === "Stale").length,
      highRisk: documents.filter((d) => d.documentRisk === "High Risk").length,
      score: Math.round(((current / total) + (reviewed / total) + (owned / total)) / 3 * 100)
    };
  }, [documents]);

  return (
    <main>
      <section className="hero">
        <p className="eyebrow">SharePoint Knowledge Agent MVP</p>
        <h1>Transparency Dashboard</h1>
        <p>AI-assisted tagging, summarization, freshness detection, and governance tracking for SharePoint knowledge content.</p>
      </section>
      <section className="cards">
        <Metric label="Total documents" value={stats.total} />
        <Metric label="Current" value={stats.current} tone="success" />
        <Metric label="Needs Review" value={stats.needsReview} tone="warning" />
        <Metric label="Stale" value={stats.stale} tone="danger" />
        <Metric label="High Risk" value={stats.highRisk} tone="danger" />
        <Metric label="Transparency Score" value={`${stats.score}%`} />
      </section>
      <section className="layout">
        <div className="panel">
          <h2>Knowledge inventory</h2>
          <table>
            <thead><tr><th>Document</th><th>Area</th><th>Freshness</th><th>Review</th></tr></thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.documentName} onClick={() => setSelected(doc)}>
                  <td>{doc.documentName}</td>
                  <td>{doc.businessArea}</td>
                  <td><span className={`pill ${doc.freshnessStatus.replace(" ", "-").toLowerCase()}`}>{doc.freshnessStatus}</span></td>
                  <td>{doc.reviewStatus}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel details">
          <h2>Document details</h2>
          {selected && (
            <>
              <h3>{selected.documentName}</h3>
              <p>{selected.summary}</p>
              <dl>
                <dt>Owner</dt><dd>{selected.contentOwner}</dd>
                <dt>Audience</dt><dd>{selected.audience}</dd>
                <dt>Risk</dt><dd>{selected.documentRisk}</dd>
                <dt>Last reviewed</dt><dd>{selected.lastReviewedDate}</dd>
              </dl>
              <div className="tags">{selected.suggestedTags.map((tag) => <span key={tag}>{tag}</span>)}</div>
            </>
          )}
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return <article className={`metric ${tone ?? ""}`}><span>{label}</span><strong>{value}</strong></article>;
}

export default App;

