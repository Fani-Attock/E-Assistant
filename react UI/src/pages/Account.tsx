import React from "react";
import { motion } from "framer-motion";
import { Link, useNavigate } from "react-router-dom";

import Footer from "../components/Footer";
import Header from "../components/Header";
import {
  fetchMarketplaceMe,
  loginMarketplaceAccount,
  registerMarketplaceAccount,
} from "../lib/api";
import { useMarketplaceSession } from "../lib/marketplaceSession";

const AccountPage: React.FC = () => {
  const navigate = useNavigate();
  const { token, user, isAuthenticated, setSession, clearSession } = useMarketplaceSession();
  const [mode, setMode] = React.useState<"login" | "register">("login");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [success, setSuccess] = React.useState("");
  const [form, setForm] = React.useState({
    full_name: "",
    email: "",
    password: "",
    role: "buyer" as "buyer" | "seller",
    store_name: "",
    bio: "",
  });

  const validate = React.useCallback((): string | null => {
    if (!form.email.trim()) {
      return "Email is required.";
    }
    if (!form.password || form.password.length < 8) {
      return "Password must be at least 8 characters.";
    }
    if (mode === "register") {
      if (!form.full_name.trim() || form.full_name.trim().length < 2) {
        return "Full name is required.";
      }
      if (form.role === "seller" && form.store_name.trim() && form.store_name.trim().length < 2) {
        return "Store name must be at least 2 characters.";
      }
    }
    return null;
  }, [form, mode]);

  React.useEffect(() => {
    if (!token || !isAuthenticated) {
      return;
    }
    fetchMarketplaceMe(token)
      .then((payload) => {
        setSession({ token, user: payload.user });
      })
      .catch(() => {
        clearSession();
      });
  }, [clearSession, isAuthenticated, setSession, token]);

  const submit = async () => {
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      setSuccess("");
      return;
    }
    setLoading(true);
    setError("");
    setSuccess("");
    try {
      if (mode === "register") {
        const payload = await registerMarketplaceAccount({
          full_name: form.full_name.trim(),
          email: form.email.trim(),
          password: form.password,
          role: form.role,
          store_name: form.store_name.trim() || undefined,
          bio: form.bio.trim() || undefined,
        });
        setSession(payload);
        setSuccess("Account created.");
        navigate(form.role === "seller" ? "/seller/dashboard" : "/store");
      } else {
        const payload = await loginMarketplaceAccount({ email: form.email.trim(), password: form.password });
        setSession(payload);
        setSuccess("Signed in.");
        navigate(payload.user.role === "seller" ? "/seller/dashboard" : "/store");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Account request failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16">
        <div className="max-w-4xl mx-auto px-6 py-12">
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            <h1 className="text-3xl font-bold text-white mb-3">Buyer and Seller Accounts</h1>
            <p className="text-sm text-[#8b949e] mb-8">
              Buyers can browse the store and use the chatbot. Sellers can create accounts and publish products directly into the marketplace catalog.
            </p>

            {isAuthenticated && user ? (
              <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-6">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-[#58a6ff] mb-2">{user.role} account</p>
                    <h2 className="text-2xl font-semibold text-white">{user.full_name}</h2>
                    <p className="text-sm text-[#8b949e] mt-1">{user.email}</p>
                    {user.store_name && <p className="text-sm text-[#c9d1d9] mt-3">Store: {user.store_name}</p>}
                    {user.bio && <p className="text-sm text-[#8b949e] mt-3 max-w-2xl">{user.bio}</p>}
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Link
                      to="/store"
                      className="px-4 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] text-sm font-semibold hover:bg-[#79b8ff] transition-all duration-200"
                    >
                      Browse Store
                    </Link>
                    {user.role === "seller" && (
                      <Link
                        to="/seller/dashboard"
                        className="px-4 py-2 rounded-lg border border-[#30363d] text-[#c9d1d9] hover:border-[#58a6ff]/40 hover:text-white transition-all duration-200"
                      >
                        Manage Products
                      </Link>
                    )}
                    <button
                      onClick={clearSession}
                      className="px-4 py-2 rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white transition-all duration-200"
                    >
                      Logout
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] gap-6">
                <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <div className="flex gap-3 mb-5">
                    <button
                      onClick={() => setMode("login")}
                      className={`px-4 py-2 rounded-lg text-sm font-medium ${
                        mode === "login" ? "bg-[#58a6ff] text-[#0d1117]" : "border border-[#30363d] text-[#8b949e]"
                      }`}
                    >
                      Sign In
                    </button>
                    <button
                      onClick={() => setMode("register")}
                      className={`px-4 py-2 rounded-lg text-sm font-medium ${
                        mode === "register" ? "bg-[#58a6ff] text-[#0d1117]" : "border border-[#30363d] text-[#8b949e]"
                      }`}
                    >
                      Create Account
                    </button>
                  </div>

                  {error && <div className="mb-4 p-3 rounded-lg border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">{error}</div>}
                  {success && <div className="mb-4 p-3 rounded-lg border border-[#1a7f37]/40 bg-[#1a7f37]/10 text-[#9be9a8] text-sm">{success}</div>}

                  <form
                    className="space-y-4"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submit();
                    }}
                  >
                    {mode === "register" && (
                      <input
                        value={form.full_name}
                        onChange={(event) => setForm((prev) => ({ ...prev, full_name: event.target.value }))}
                        placeholder="Full name"
                        className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                      />
                    )}
                    <input
                      value={form.email}
                      onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))}
                      placeholder="Email"
                      className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                    />
                    <input
                      type="password"
                      value={form.password}
                      onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
                      placeholder="Password"
                      className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                    />
                    {mode === "register" && (
                      <>
                        <select
                          value={form.role}
                          onChange={(event) => setForm((prev) => ({ ...prev, role: event.target.value as "buyer" | "seller" }))}
                          className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                        >
                          <option value="buyer">Buyer account</option>
                          <option value="seller">Seller account</option>
                        </select>
                        {form.role === "seller" && (
                          <input
                            value={form.store_name}
                            onChange={(event) => setForm((prev) => ({ ...prev, store_name: event.target.value }))}
                            placeholder="Store name"
                            className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                          />
                        )}
                        <textarea
                          value={form.bio}
                          onChange={(event) => setForm((prev) => ({ ...prev, bio: event.target.value }))}
                          placeholder="Short bio or store description"
                          rows={4}
                          className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-4 py-3 outline-none focus:border-[#58a6ff]/50"
                        />
                      </>
                    )}
                    <button
                      type="submit"
                      disabled={loading}
                      className="w-full px-4 py-3 rounded-lg bg-[#58a6ff] text-[#0d1117] text-sm font-semibold hover:bg-[#79b8ff] disabled:opacity-50 transition-all duration-200"
                    >
                      {loading ? "Please wait..." : mode === "register" ? "Create account" : "Sign in"}
                    </button>
                  </form>
                </div>

                <div className="rounded-2xl border border-[#30363d] bg-[#161b22] p-6">
                  <h2 className="text-xl font-semibold text-white mb-4">What each account can do</h2>
                  <div className="space-y-4 text-sm text-[#8b949e]">
                    <div>
                      <p className="text-white font-medium mb-1">Buyer</p>
                      <p>Browse the full store catalog, open product details, and use the chatbot to ask for recommendations or comparisons.</p>
                    </div>
                    <div>
                      <p className="text-white font-medium mb-1">Seller</p>
                      <p>Create a storefront identity, publish products into the marketplace, update prices and stock, and still use the same chatbot/search experience.</p>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default AccountPage;
