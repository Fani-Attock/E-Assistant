import React, { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Clock, FileText, History as HistoryIcon, PlayCircle, Search, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";

import Footer from "../components/Footer";
import Header from "../components/Header";
import { deleteConversation, deleteSavedReport, downloadSavedReportPdf, fetchSavedReports } from "../lib/api";
import { useMarketplaceSession } from "../lib/marketplaceSession";
import {
  clearRecentSessions,
  getOrCreateUserId,
  listRecentSessions,
  removeRecentSession,
} from "../lib/sessionStore";
import type { SavedReport } from "../types/api";
import type { ConversationSummary } from "../types/chat";

function formatDate(msOrIso: number | string | null | undefined): string {
  const dt = typeof msOrIso === "number" ? new Date(msOrIso) : new Date(msOrIso || "");
  if (Number.isNaN(dt.getTime())) {
    return "Unknown time";
  }
  return dt.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const History: React.FC = () => {
  const localUserId = useMemo(() => getOrCreateUserId(), []);
  const { user } = useMarketplaceSession();
  const [activeTab, setActiveTab] = useState<"conversations" | "reports">("conversations");
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [busyId, setBusyId] = useState<string>("");
  const [error, setError] = useState("");

  const reportOwners = useMemo(() => {
    const owners = [localUserId];
    if (user?.user_id && user.user_id !== localUserId) {
      owners.push(user.user_id);
    }
    return owners;
  }, [localUserId, user?.user_id]);

  const refreshSessions = useCallback(() => {
    setItems(listRecentSessions());
  }, []);

  const refreshReports = useCallback(async () => {
    setError("");
    try {
      const payloads = await Promise.all(reportOwners.map((ownerId) => fetchSavedReports({ userId: ownerId })));
      const merged = payloads.flatMap((payload) => payload.items || []);
      const dedup = merged.filter((row, idx) => merged.findIndex((x) => x.report_id === row.report_id) === idx);
      dedup.sort((a, b) => {
        const left = Date.parse(a.created_at || "");
        const right = Date.parse(b.created_at || "");
        return (Number.isNaN(right) ? 0 : right) - (Number.isNaN(left) ? 0 : left);
      });
      setReports(dedup);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load saved reports.");
    }
  }, [reportOwners]);

  useEffect(() => {
    refreshSessions();
    void refreshReports();
  }, [refreshReports, refreshSessions]);

  const handleDeleteConversation = useCallback(
    async (conversationId: string) => {
      setBusyId(conversationId);
      setError("");
      try {
        await deleteConversation({ conversationId, userId: localUserId });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete conversation from backend.");
      } finally {
        removeRecentSession(conversationId);
        refreshSessions();
        setBusyId("");
      }
    },
    [localUserId, refreshSessions]
  );

  const handleClearAll = useCallback(async () => {
    const rows = listRecentSessions();
    setError("");
    for (const row of rows) {
      try {
        await deleteConversation({ conversationId: row.conversationId, userId: localUserId });
      } catch {
        // Keep clearing local state even if backend row is already missing.
      }
    }
    clearRecentSessions();
    refreshSessions();
  }, [localUserId, refreshSessions]);

  const handleDeleteReport = useCallback(async (report: SavedReport) => {
    setBusyId(report.report_id);
    setError("");
    try {
      await deleteSavedReport({ reportId: report.report_id, userId: report.owner_user_id });
      await refreshReports();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete report.");
    } finally {
      setBusyId("");
    }
  }, [refreshReports]);

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16" id="main-content">
        <div className="max-w-5xl mx-auto px-6 py-16">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
            <div className="flex items-center gap-3 mb-2">
              <div className="w-10 h-10 rounded-xl bg-[#58a6ff]/10 border border-[#58a6ff]/20 flex items-center justify-center">
                <HistoryIcon size={20} className="text-[#58a6ff]" />
              </div>
              <h1 className="font-heading text-3xl font-bold text-white">History</h1>
            </div>
            <p className="text-[#8b949e] text-sm mb-8 ml-13">
              Conversations and saved reports from search, assistant analysis, and seller analytics.
            </p>

            {error && (
              <div className="mb-4 p-3 rounded-lg border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">
                {error}
              </div>
            )}

            <div className="flex items-center gap-2 mb-6">
              {(["conversations", "reports"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 rounded-lg text-sm border transition-all duration-200 ${
                    activeTab === tab
                      ? "border-[#58a6ff]/50 bg-[#58a6ff]/10 text-[#58a6ff]"
                      : "border-[#30363d] text-[#8b949e] hover:text-white"
                  }`}
                >
                  {tab === "conversations" ? "Conversations" : "Reports"}
                </button>
              ))}
            </div>

            {activeTab === "conversations" ? (
              <>
                <div className="space-y-3">
                  {items.length === 0 && (
                    <div className="p-6 bg-[#161b22] border border-[#30363d] rounded-xl text-[#8b949e] text-sm">
                      No stored sessions yet. Start from <Link className="text-[#58a6ff] underline" to="/">Search</Link>.
                    </div>
                  )}
                  {items.map((item, i) => (
                    <motion.div
                      key={item.conversationId}
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.05 }}
                      className="group flex items-center gap-4 p-4 bg-[#161b22] border border-[#30363d] rounded-xl hover:border-[#58a6ff]/30 hover:bg-[#161b22]/80 transition-all duration-200"
                    >
                      <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-[#0d1117] border border-[#30363d] flex items-center justify-center">
                        <Search size={16} className="text-[#8b949e]" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-[#e6edf3] text-sm font-medium truncate">{item.query}</p>
                        <div className="flex items-center gap-3 mt-0.5">
                          <span className="text-xs text-[#484f58] flex items-center gap-1">
                            <Clock size={10} />
                            {formatDate(item.timestamp)}
                          </span>
                          <span className="text-xs text-[#3fb950]">{item.resultCount} results</span>
                          <span className="text-xs text-[#8b949e] truncate max-w-[220px]">ID: {item.conversationId}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/?conversation=${encodeURIComponent(item.conversationId)}`}
                          className="p-2 rounded-lg text-[#58a6ff] hover:bg-[#58a6ff]/10 border border-[#58a6ff]/30 transition-all duration-200"
                          aria-label={`Resume conversation ${item.conversationId}`}
                        >
                          <PlayCircle size={14} />
                        </Link>
                        <button
                          disabled={busyId === item.conversationId}
                          onClick={() => void handleDeleteConversation(item.conversationId)}
                          className="p-2 rounded-lg text-[#484f58] hover:text-[#f85149] hover:bg-[#f85149]/10 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] disabled:opacity-40"
                          aria-label={`Delete conversation ${item.conversationId}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </motion.div>
                  ))}
                </div>

                {items.length > 0 && (
                  <div className="mt-8 flex justify-center">
                    <button
                      onClick={() => void handleClearAll()}
                      className="px-4 py-2 rounded-lg border border-[#30363d] text-sm text-[#8b949e] hover:text-[#f85149] hover:border-[#f85149]/40 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
                    >
                      Clear All History
                    </button>
                  </div>
                )}
              </>
            ) : (
              <div className="space-y-3">
                {reports.length === 0 && (
                  <div className="p-6 bg-[#161b22] border border-[#30363d] rounded-xl text-[#8b949e] text-sm">
                    No saved reports yet. Generate one from the assistant or seller dashboard.
                  </div>
                )}
                {reports.map((report, i) => (
                  <motion.div
                    key={report.report_id}
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className="group flex items-center gap-4 p-4 bg-[#161b22] border border-[#30363d] rounded-xl hover:border-[#58a6ff]/30 hover:bg-[#161b22]/80 transition-all duration-200"
                  >
                    <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-[#0d1117] border border-[#30363d] flex items-center justify-center">
                      <FileText size={16} className="text-[#8b949e]" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[#e6edf3] text-sm font-medium truncate">{report.title}</p>
                      <div className="flex flex-wrap items-center gap-3 mt-0.5">
                        <span className="text-xs text-[#484f58] flex items-center gap-1">
                          <Clock size={10} />
                          {formatDate(report.created_at)}
                        </span>
                        <span className="text-xs text-[#58a6ff]">{report.report_type}</span>
                        <span className="text-xs text-[#8b949e] truncate max-w-[220px]">Owner: {report.owner_user_id}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/reports/${encodeURIComponent(report.report_id)}?owner=${encodeURIComponent(report.owner_user_id)}`}
                        className="p-2 rounded-lg text-[#58a6ff] hover:bg-[#58a6ff]/10 border border-[#58a6ff]/30 transition-all duration-200"
                        aria-label={`Open report ${report.report_id}`}
                      >
                        <PlayCircle size={14} />
                      </Link>
                      <button
                        onClick={() => void downloadSavedReportPdf({ reportId: report.report_id, userId: report.owner_user_id })}
                        className="p-2 rounded-lg text-[#c9d1d9] hover:bg-white/5 border border-[#30363d] transition-all duration-200"
                        aria-label={`Download PDF ${report.report_id}`}
                      >
                        PDF
                      </button>
                      <button
                        disabled={busyId === report.report_id}
                        onClick={() => void handleDeleteReport(report)}
                        className="p-2 rounded-lg text-[#484f58] hover:text-[#f85149] hover:bg-[#f85149]/10 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] disabled:opacity-40"
                        aria-label={`Delete report ${report.report_id}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </motion.div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default History;
