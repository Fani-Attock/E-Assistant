import React from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Download } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import Footer from "../components/Footer";
import Header from "../components/Header";
import { downloadSavedReportPdf, fetchSavedReport } from "../lib/api";
import { useMarketplaceSession } from "../lib/marketplaceSession";
import { getOrCreateUserId } from "../lib/sessionStore";
import type { SavedReport } from "../types/api";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

const ReportDetail: React.FC = () => {
  const { reportId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const localUserId = React.useMemo(() => getOrCreateUserId(), []);
  const { user } = useMarketplaceSession();
  const ownerId = (searchParams.get("owner") || user?.user_id || localUserId).trim();
  const [report, setReport] = React.useState<SavedReport | null>(null);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let active = true;
    const run = async () => {
      setLoading(true);
      setError("");
      try {
        const payload = await fetchSavedReport({ reportId, userId: ownerId });
        if (active) {
          setReport(payload.report);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load report.");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };
    if (reportId && ownerId) {
      void run();
    }
    return () => {
      active = false;
    };
  }, [ownerId, reportId]);

  const payload = asRecord(report?.payload);
  const summary = asRecord(payload.summary);
  const filters = asRecord(payload.filters);
  const products = Array.isArray(payload.products) ? payload.products : [];
  const notes = Array.isArray(payload.notes) ? payload.notes : [];

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16" id="main-content">
        <div className="max-w-5xl mx-auto px-6 py-12">
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            <div className="flex items-center justify-between gap-4 mb-6">
              <div>
                <Link to="/history" className="inline-flex items-center gap-2 text-sm text-[#58a6ff] hover:underline mb-3">
                  <ArrowLeft size={14} />
                  Back to history
                </Link>
                <h1 className="text-3xl font-bold text-white">{report?.title || "Saved Report"}</h1>
                <p className="text-sm text-[#8b949e] mt-2">
                  {report?.report_type || "report"} {report?.created_at ? `| Generated ${new Date(report.created_at).toLocaleString()}` : ""}
                </p>
              </div>
              {report && (
                <button
                  onClick={() => void downloadSavedReportPdf({ reportId: report.report_id, userId: ownerId })}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] font-semibold hover:bg-[#79b8ff]"
                >
                  <Download size={16} />
                  Download PDF
                </button>
              )}
            </div>

            {loading ? (
              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-8 text-[#8b949e]">Loading report...</div>
            ) : error ? (
              <div className="rounded-2xl border border-[#f85149]/40 bg-[#f85149]/10 p-6 text-[#ffb3b3]">{error}</div>
            ) : report ? (
              <div className="space-y-6">
                <section className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <h2 className="text-xl font-semibold text-white mb-4">Context</h2>
                  <div className="grid md:grid-cols-2 gap-3 text-sm text-[#c9d1d9]">
                    <div>Owner: {report.owner_user_id}</div>
                    <div>Source: {report.source_kind || "report"}</div>
                    <div>Conversation: {report.conversation_id || "N/A"}</div>
                    <div>Seller: {report.seller_id || "N/A"}</div>
                    {Object.entries(filters).map(([key, value]) => (
                      <div key={key}>
                        {key}: {String(value)}
                      </div>
                    ))}
                  </div>
                </section>

                <section className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <h2 className="text-xl font-semibold text-white mb-4">Summary</h2>
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {Object.entries(summary).map(([key, value]) => (
                      <div key={key} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                        <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">{key.replace(/_/g, " ")}</p>
                        <p className="text-lg font-semibold text-white break-words">{String(value)}</p>
                      </div>
                    ))}
                  </div>
                </section>

                <section className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <h2 className="text-xl font-semibold text-white mb-4">Products</h2>
                  <div className="space-y-3">
                    {products.length === 0 && <div className="text-sm text-[#8b949e]">No product rows in this report.</div>}
                    {products.map((row, index) => {
                      const item = asRecord(row);
                      return (
                        <div key={`${String(item.product_id || item.title || "product")}_${index}`} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                          <div className="flex flex-col gap-2">
                            <p className="text-white font-medium">{String(item.title || item.product_id || "Product")}</p>
                            <p className="text-xs text-[#8b949e]">
                              Source rating: {item.source_rating ?? item.rating ?? "N/A"} ({item.source_review_count ?? item.review_count ?? 0})
                              {" | "}App rating: {item.app_rating ?? "N/A"} ({item.app_review_count ?? 0})
                            </p>
                            <p className="text-xs text-[#8b949e]">
                              Units sold: {String(item.units_sold ?? 0)} | Orders: {String(item.order_count ?? 0)} | Revenue PKR: {String(item.revenue_pkr ?? 0)}
                            </p>
                            <p className="text-xs text-[#8b949e]">
                              Predicted rating: {String(item.predicted_app_rating ?? "N/A")} | Demand: {String(item.predicted_demand_score ?? "N/A")} | Seasonal score: {String(item.seasonal_relevance_score ?? "N/A")}
                            </p>
                            <p className="text-xs text-[#58a6ff]">
                              Best months: {Array.isArray(item.best_month_labels) && item.best_month_labels.length > 0 ? item.best_month_labels.join(", ") : "N/A"}
                            </p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <section className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <h2 className="text-xl font-semibold text-white mb-4">Notes</h2>
                  <div className="space-y-2 text-sm text-[#c9d1d9]">
                    {notes.length === 0 && <div className="text-[#8b949e]">No notes attached.</div>}
                    {notes.map((note, index) => (
                      <p key={index}>{String(note)}</p>
                    ))}
                  </div>
                </section>
              </div>
            ) : null}
          </motion.div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default ReportDetail;
