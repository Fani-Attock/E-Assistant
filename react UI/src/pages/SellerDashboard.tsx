import React from "react";
import { motion } from "framer-motion";
import { Link, useNavigate } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";

import Footer from "../components/Footer";
import Header from "../components/Header";
import {
  createSellerProduct,
  downloadSavedReportPdf,
  fetchSavedReports,
  fetchSellerOrders,
  deleteSellerProduct,
  fetchSellerProducts,
  fetchSellerReportSummary,
  updateSellerOrderStatus,
  updateSellerProduct,
} from "../lib/api";
import { useMarketplaceSession } from "../lib/marketplaceSession";
import { mapStoreProductToCard } from "../lib/storeMapper";
import ProductCard from "../components/ProductCard";
import type { SavedReport, StoreProduct } from "../types/api";

const emptyForm = {
  title: "",
  description: "",
  category: "",
  subcategory: "",
  brand: "",
  model: "",
  price_pkr: 0,
  shipping_pkr: 0,
  stock_qty: 0,
  in_stock: true,
  specifications: "",
  images: "",
  tags: "",
  external_url: "",
};

const SellerDashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const { token, user, isAuthenticated } = useMarketplaceSession();
  const [items, setItems] = React.useState<StoreProduct[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [success, setSuccess] = React.useState("");
  const [report, setReport] = React.useState<Record<string, unknown> | null>(null);
  const [savedReports, setSavedReports] = React.useState<SavedReport[]>([]);
  const [orders, setOrders] = React.useState<Record<string, unknown>[]>([]);
  const [editingId, setEditingId] = React.useState<string>("");
  const [form, setForm] = React.useState({ ...emptyForm });

  const load = React.useCallback(async () => {
    if (!token) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await fetchSellerProducts(token);
      setItems(payload.items);
      const [reportPayload, ordersPayload] = await Promise.all([fetchSellerReportSummary(token), fetchSellerOrders(token)]);
      setReport(reportPayload);
      setOrders(ordersPayload.items);
      if (user?.user_id) {
        const reportsPayload = await fetchSavedReports({ userId: user.user_id, reportType: "seller_summary" });
        setSavedReports(reportsPayload.items || []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load seller products.");
    } finally {
      setLoading(false);
    }
  }, [token, user?.user_id]);

  React.useEffect(() => {
    if (!isAuthenticated || user?.role !== "seller") {
      navigate("/account");
      return;
    }
    void load();
  }, [isAuthenticated, load, navigate, user?.role]);

  const resetForm = React.useCallback(() => {
    setForm({ ...emptyForm });
    setEditingId("");
  }, []);

  const handleSave = async () => {
    if (!token) {
      return;
    }
    setSaving(true);
    setError("");
    setSuccess("");
    if (!form.title.trim()) {
      setSaving(false);
      setError("title: Product title is required.");
      return;
    }
    if (!Number.isFinite(Number(form.price_pkr)) || Number(form.price_pkr) < 0) {
      setSaving(false);
      setError("price_pkr: Price must be zero or greater.");
      return;
    }
    if (!Number.isFinite(Number(form.shipping_pkr)) || Number(form.shipping_pkr) < 0) {
      setSaving(false);
      setError("shipping_pkr: Shipping must be zero or greater.");
      return;
    }
    if (!Number.isFinite(Number(form.stock_qty)) || Number(form.stock_qty) < 0) {
      setSaving(false);
      setError("stock_qty: Stock quantity must be zero or greater.");
      return;
    }
    const imageRows = form.images
      .split("\n")
      .map((row) => row.trim())
      .filter(Boolean);
    const tagRows = form.tags
      .split(",")
      .map((row) => row.trim())
      .filter(Boolean);
    if (imageRows.length > 8) {
      setSaving(false);
      setError("images: A maximum of 8 image URLs is allowed.");
      return;
    }
    if (tagRows.length > 24) {
      setSaving(false);
      setError("tags: A maximum of 24 tags is allowed.");
      return;
    }
    if (form.external_url.trim()) {
      try {
        const parsed = new URL(form.external_url.trim());
        if (!/^https?:$/i.test(parsed.protocol)) {
          throw new Error("invalid");
        }
      } catch {
        setSaving(false);
        setError("external_url: URL must start with http:// or https://.");
        return;
      }
    }
    const payload = {
      title: form.title,
      description: form.description || undefined,
      category: form.category || undefined,
      subcategory: form.subcategory || undefined,
      brand: form.brand || undefined,
      model: form.model || undefined,
      price_pkr: Number(form.price_pkr),
      shipping_pkr: Number(form.shipping_pkr),
      stock_qty: Number(form.stock_qty),
      in_stock: form.in_stock,
      specifications: form.specifications || undefined,
      images: imageRows,
      tags: tagRows,
      external_url: form.external_url || undefined,
    };
    try {
      if (editingId) {
        await updateSellerProduct(token, editingId, payload);
        setSuccess("Product updated.");
      } else {
        await createSellerProduct(token, payload);
        setSuccess("Product created.");
      }
      resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save product.");
    } finally {
      setSaving(false);
    }
  };

  const handleOrderStatus = async (orderId: string, status: "pending" | "paid" | "fulfilled" | "cancelled") => {
    if (!token) {
      return;
    }
    setError("");
    setSuccess("");
    try {
      await updateSellerOrderStatus(token, orderId, status);
      setSuccess(`Order updated to ${status}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update order.");
    }
  };

  const handleEdit = (item: StoreProduct) => {
    setEditingId(item.product_id);
    setForm({
      title: item.title,
      description: item.description || "",
      category: item.category || "",
      subcategory: item.subcategory || "",
      brand: item.brand || "",
      model: item.model || "",
      price_pkr: Number(item.price_pkr || 0),
      shipping_pkr: Number(item.shipping_pkr || 0),
      stock_qty: Number(item.stock_qty || 0),
      in_stock: item.in_stock,
      specifications: item.specifications || "",
      images: (item.images || []).join("\n"),
      tags: (item.tags || []).join(", "),
      external_url: item.external_url || "",
    });
  };

  const handleDelete = async (productId: string) => {
    if (!token) {
      return;
    }
    setError("");
    setSuccess("");
    try {
      await deleteSellerProduct(token, productId);
      if (editingId === productId) {
        resetForm();
      }
      setSuccess("Product deleted.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete product.");
    }
  };

  const reportSummary = (report?.summary as Record<string, unknown> | undefined) || undefined;
  const reportProducts = Array.isArray(report?.products) ? (report.products as Record<string, unknown>[]) : [];

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16">
        <div className="max-w-7xl mx-auto px-6 py-10">
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6 mb-8">
              <div>
                <h1 className="text-3xl font-bold text-white mb-2">Seller Dashboard</h1>
                <p className="text-sm text-[#8b949e] max-w-2xl">
                  Manage your marketplace products. Published items are searchable in the store and the chatbot.
                </p>
              </div>
              <Link
                to="/store?listing_type=seller"
                className="px-4 py-2 rounded-lg border border-[#30363d] text-[#c9d1d9] text-sm hover:border-[#58a6ff]/40 hover:text-white transition-all duration-200"
              >
                View Seller Listings in Store
              </Link>
            </div>

            {error && <div className="mb-4 p-3 rounded-lg border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">{error}</div>}
            {success && <div className="mb-4 p-3 rounded-lg border border-[#1a7f37]/40 bg-[#1a7f37]/10 text-[#9be9a8] text-sm">{success}</div>}

            <div className="grid xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] gap-6">
              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                <div className="flex items-center justify-between mb-5">
                  <div>
                    <h2 className="text-xl font-semibold text-white">{editingId ? "Edit Product" : "Add Product"}</h2>
                    <p className="text-xs text-[#8b949e] mt-1">Use real product data. These fields are stored in Mongo and surfaced in store search.</p>
                  </div>
                  {editingId && (
                    <button onClick={resetForm} className="text-sm text-[#58a6ff] hover:underline">
                      Cancel edit
                    </button>
                  )}
                </div>

                <div className="grid md:grid-cols-2 gap-4">
                  <input value={form.title} onChange={(e) => setForm((p) => ({ ...p, title: e.target.value }))} placeholder="Product title" className="md:col-span-2 rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input value={form.category} onChange={(e) => setForm((p) => ({ ...p, category: e.target.value }))} placeholder="Category" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input value={form.subcategory} onChange={(e) => setForm((p) => ({ ...p, subcategory: e.target.value }))} placeholder="Subcategory" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input value={form.brand} onChange={(e) => setForm((p) => ({ ...p, brand: e.target.value }))} placeholder="Brand" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input value={form.model} onChange={(e) => setForm((p) => ({ ...p, model: e.target.value }))} placeholder="Model" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input type="number" value={form.price_pkr} onChange={(e) => setForm((p) => ({ ...p, price_pkr: Number(e.target.value) }))} placeholder="Price PKR" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input type="number" value={form.shipping_pkr} onChange={(e) => setForm((p) => ({ ...p, shipping_pkr: Number(e.target.value) }))} placeholder="Shipping PKR" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input type="number" value={form.stock_qty} onChange={(e) => setForm((p) => ({ ...p, stock_qty: Number(e.target.value), in_stock: Number(e.target.value) > 0 }))} placeholder="Stock quantity" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <input value={form.external_url} onChange={(e) => setForm((p) => ({ ...p, external_url: e.target.value }))} placeholder="External product URL (optional)" className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <textarea value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} placeholder="Short description" rows={4} className="md:col-span-2 rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <textarea value={form.specifications} onChange={(e) => setForm((p) => ({ ...p, specifications: e.target.value }))} placeholder="Specifications or feature highlights" rows={5} className="md:col-span-2 rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <textarea value={form.images} onChange={(e) => setForm((p) => ({ ...p, images: e.target.value }))} placeholder="Image URLs, one per line" rows={4} className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                  <textarea value={form.tags} onChange={(e) => setForm((p) => ({ ...p, tags: e.target.value }))} placeholder="Tags, comma separated" rows={4} className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50" />
                </div>

                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="mt-5 inline-flex items-center gap-2 px-4 py-3 rounded-lg bg-[#58a6ff] text-[#0d1117] text-sm font-semibold hover:bg-[#79b8ff] disabled:opacity-50 transition-all duration-200"
                >
                  <Plus size={16} />
                  {saving ? "Saving..." : editingId ? "Update product" : "Publish product"}
                </button>
              </div>

              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                <div className="flex items-center justify-between mb-5">
                  <div>
                    <h2 className="text-xl font-semibold text-white">Your Products</h2>
                    <p className="text-xs text-[#8b949e] mt-1">These items are live in the store and included in search.</p>
                  </div>
                  {!loading && <span className="text-sm text-[#8b949e]">{items.length} items</span>}
                </div>

                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 3 }).map((_, index) => (
                      <div key={index} className="h-28 rounded-xl bg-[#0d1117] border border-[#30363d] animate-pulse" />
                    ))}
                  </div>
                ) : items.length > 0 ? (
                  <div className="space-y-6">
                    {items.map((item) => (
                      <div key={item.product_id} className="space-y-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => handleEdit(item)}
                            className="inline-flex items-center gap-1 px-3 py-2 rounded-lg border border-[#30363d] text-xs text-[#c9d1d9] hover:border-[#58a6ff]/40"
                          >
                            <Pencil size={12} />
                            Edit
                          </button>
                          <button
                            onClick={() => void handleDelete(item.product_id)}
                            className="inline-flex items-center gap-1 px-3 py-2 rounded-lg border border-[#30363d] text-xs text-[#f85149] hover:border-[#f85149]/40"
                          >
                            <Trash2 size={12} />
                            Delete
                          </button>
                        </div>
                        <ProductCard product={mapStoreProductToCard(item)} rank={1} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-6 rounded-xl border border-[#30363d] bg-[#0d1117] text-[#8b949e] text-sm">
                    No seller products yet. Add your first listing from the form.
                  </div>
                )}
              </div>
            </div>

            <div className="grid xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-6 mt-6">
              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                <h2 className="text-xl font-semibold text-white mb-5">Sales and Ratings Report</h2>
                {report ? (
                  <>
                    <div className="grid sm:grid-cols-2 gap-4 mb-6">
                      <div className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                        <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">Units sold</p>
                        <p className="text-2xl font-bold text-[#c9d1d9]">{Number(reportSummary?.units_sold || 0).toLocaleString()}</p>
                      </div>
                      <div className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                        <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">Revenue</p>
                        <p className="text-2xl font-bold text-[#3fb950]">PKR {Number(reportSummary?.revenue_pkr || 0).toLocaleString()}</p>
                      </div>
                    </div>
                    <div className="space-y-3">
                      {reportProducts.slice(0, 6).map((row) => {
                          const item = row as Record<string, unknown>;
                          return (
                            <div key={String(item.product_id || item.title)} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                              <div className="flex items-start justify-between gap-4">
                                <div>
                                  <p className="text-white font-medium">{String(item.title || "Product")}</p>
                                  <p className="text-xs text-[#8b949e] mt-1">
                                    Sold {Number(item.units_sold || 0).toLocaleString()} units | Revenue PKR {Number(item.revenue_pkr || 0).toLocaleString()}
                                  </p>
                                  <p className="text-xs text-[#8b949e] mt-1">
                                    Predicted rating {item.predicted_app_rating == null ? "N/A" : Number(item.predicted_app_rating).toFixed(1)} | Demand {item.predicted_demand_score == null ? "N/A" : Number(item.predicted_demand_score).toFixed(2)}
                                  </p>
                                </div>
                                <div className="text-right text-xs text-[#58a6ff]">
                                  {Array.isArray(item.best_month_labels) && item.best_month_labels.length > 0
                                    ? item.best_month_labels.join(", ")
                                    : "No month trend yet"}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                    </div>
                  </>
                ) : (
                  <div className="text-sm text-[#8b949e]">Analytics will appear after products and orders are loaded.</div>
                )}
              </div>

              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                <h2 className="text-xl font-semibold text-white mb-5">Orders</h2>
                {orders.length > 0 ? (
                  <div className="space-y-3">
                    {orders.map((row) => {
                      const order = row as Record<string, unknown>;
                      return (
                        <div key={String(order.order_id)} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
                            <div>
                              <p className="text-white font-medium">{String(order.title || "Order")}</p>
                              <p className="text-xs text-[#8b949e] mt-1">
                                Qty {Number(order.quantity || 0)} | Total PKR {Number(order.total_pkr || 0).toLocaleString()} | Buyer {String(order.buyer_name || "Buyer")}
                              </p>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-[#8b949e]">Status</span>
                              {(["pending", "paid", "fulfilled", "cancelled"] as const).map((status) => (
                                <button
                                  key={status}
                                  onClick={() => void handleOrderStatus(String(order.order_id || ""), status)}
                                  className={`px-2 py-1 rounded-lg text-xs border ${
                                    String(order.status) === status
                                      ? "border-[#58a6ff]/50 text-white bg-[#58a6ff]/10"
                                      : "border-[#30363d] text-[#8b949e]"
                                  }`}
                                >
                                  {status}
                                </button>
                              ))}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="p-6 rounded-xl border border-[#30363d] bg-[#0d1117] text-[#8b949e] text-sm">
                    No orders yet. When buyers order through the marketplace, sales and seasonal reports will populate here.
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6 mt-6">
              <div className="flex items-center justify-between mb-5">
                <div>
                  <h2 className="text-xl font-semibold text-white">Saved Reports</h2>
                  <p className="text-xs text-[#8b949e] mt-1">Reports generated from this dashboard are saved automatically.</p>
                </div>
                <Link to="/history" className="text-sm text-[#58a6ff] hover:underline">
                  Open full history
                </Link>
              </div>
              {savedReports.length > 0 ? (
                <div className="space-y-3">
                  {savedReports.slice(0, 8).map((saved) => (
                    <div key={saved.report_id} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4 flex items-center justify-between gap-4">
                      <div>
                        <p className="text-white font-medium">{saved.title}</p>
                        <p className="text-xs text-[#8b949e] mt-1">
                          {saved.created_at ? new Date(saved.created_at).toLocaleString() : "Unknown time"}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/reports/${encodeURIComponent(saved.report_id)}?owner=${encodeURIComponent(saved.owner_user_id)}`}
                          className="px-3 py-2 rounded-lg border border-[#58a6ff]/30 text-sm text-[#58a6ff] hover:bg-[#58a6ff]/10"
                        >
                          Open
                        </Link>
                        <button
                          onClick={() => void downloadSavedReportPdf({ reportId: saved.report_id, userId: saved.owner_user_id })}
                          className="px-3 py-2 rounded-lg border border-[#30363d] text-sm text-[#c9d1d9] hover:bg-white/5"
                        >
                          PDF
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-6 rounded-xl border border-[#30363d] bg-[#0d1117] text-[#8b949e] text-sm">
                  No saved reports yet. Reload analytics or place orders to generate seller reports.
                </div>
              )}
            </div>
          </motion.div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default SellerDashboardPage;
