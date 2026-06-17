import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Bot, LogIn, Menu, Store, UserRound, X } from 'lucide-react';

import { useMarketplaceSession } from '../lib/marketplaceSession';

const baseNavLinks = [
  { label: 'Search', href: '/' },
  { label: 'Store', href: '/store' },
  { label: 'History', href: '/history' },
  { label: 'About', href: '/about' },
];

const Header: React.FC = () => {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { user, isAuthenticated, clearSession } = useMarketplaceSession();

  const navLinks = [
    ...baseNavLinks,
    ...(user?.role === 'seller' ? [{ label: 'Seller Dashboard', href: '/seller/dashboard' }] : []),
  ];

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? 'bg-[#0d1117]/95 backdrop-blur-md border-b border-[#30363d]'
          : 'bg-transparent backdrop-blur-sm'
      }`}
    >
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link
          to="/"
          className="flex items-center gap-2.5 group focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] rounded-lg"
          aria-label="E-Assistant home"
        >
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#58a6ff] to-[#7c3aed] flex items-center justify-center shadow-lg group-hover:scale-105 transition-transform duration-200">
            <Bot size={16} className="text-white" />
          </div>
          <span className="font-heading text-lg font-bold text-white tracking-tight">
            E-<span className="text-[#58a6ff]">Assistant</span>
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-1" aria-label="Primary navigation">
          {navLinks.map((link) => {
            const isActive = location.pathname === link.href;
            return (
              <Link
                key={link.href}
                to={link.href}
                className={`relative px-4 py-2 text-sm font-medium rounded-lg transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] ${
                  isActive
                    ? 'text-[#58a6ff] bg-[#58a6ff]/10'
                    : 'text-[#8b949e] hover:text-white hover:bg-white/5'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="hidden md:flex items-center gap-2">
          {isAuthenticated && user ? (
            <>
              <Link
                to="/account"
                className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-[#30363d] text-[#c9d1d9] hover:text-white hover:border-[#58a6ff]/40 transition-all duration-200"
              >
                <UserRound size={14} />
                {user.role === 'seller' ? (user.store_name || user.full_name) : user.full_name}
              </Link>
              <button
                onClick={clearSession}
                className="px-3 py-2 text-sm rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white hover:border-[#58a6ff]/40 transition-all duration-200"
              >
                Logout
              </button>
            </>
          ) : (
            <Link
              to="/account"
              className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-[#30363d] text-[#c9d1d9] hover:text-white hover:border-[#58a6ff]/40 transition-all duration-200"
            >
              <LogIn size={14} />
              Account
            </Link>
          )}
        </div>

        <button
          className="md:hidden p-2 rounded-lg text-[#8b949e] hover:text-white hover:bg-white/5 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
          onClick={() => setMobileOpen((v) => !v)}
          aria-expanded={mobileOpen}
          aria-controls="mobile-nav"
          aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
        >
          {mobileOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      {/* Mobile drawer */}
      <div
        id="mobile-nav"
        role="dialog"
        aria-modal="true"
        aria-label="Mobile navigation"
        className={`md:hidden transition-all duration-300 overflow-hidden ${
          mobileOpen ? 'max-h-80 opacity-100' : 'max-h-0 opacity-0'
        } bg-[#0d1117]/98 backdrop-blur-md border-b border-[#30363d]`}
      >
        <nav className="px-6 py-4 flex flex-col gap-1">
          {navLinks.map((link) => {
            const isActive = location.pathname === link.href;
            return (
              <Link
                key={link.href}
                to={link.href}
                className={`px-4 py-3 text-sm font-medium rounded-lg transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] ${
                  isActive
                    ? 'text-[#58a6ff] bg-[#58a6ff]/10'
                    : 'text-[#8b949e] hover:text-white hover:bg-white/5'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                {link.label}
              </Link>
            );
          })}
          <div className="pt-3 mt-3 border-t border-[#30363d]">
            {isAuthenticated && user ? (
              <>
                <Link
                  to="/account"
                  className="px-4 py-3 text-sm font-medium rounded-lg transition-all duration-200 flex items-center gap-2 text-[#8b949e] hover:text-white hover:bg-white/5"
                >
                  {user.role === 'seller' ? <Store size={16} /> : <UserRound size={16} />}
                  {user.role === 'seller' ? (user.store_name || user.full_name) : user.full_name}
                </Link>
                <button
                  onClick={clearSession}
                  className="w-full text-left px-4 py-3 text-sm font-medium rounded-lg transition-all duration-200 text-[#8b949e] hover:text-white hover:bg-white/5"
                >
                  Logout
                </button>
              </>
            ) : (
              <Link
                to="/account"
                className="px-4 py-3 text-sm font-medium rounded-lg transition-all duration-200 flex items-center gap-2 text-[#8b949e] hover:text-white hover:bg-white/5"
              >
                <LogIn size={16} />
                Account
              </Link>
            )}
          </div>
        </nav>
      </div>
    </header>
  );
};

export default Header;
