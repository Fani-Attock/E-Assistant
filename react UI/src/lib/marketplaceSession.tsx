import React from "react";

import type { MarketplaceAuthResponse, MarketplaceUser } from "../types/api";

const TOKEN_KEY = "eassistant_marketplace_token_v1";
const USER_KEY = "eassistant_marketplace_user_v1";

type MarketplaceSessionContextValue = {
  token: string;
  user: MarketplaceUser | null;
  isAuthenticated: boolean;
  setSession: (payload: MarketplaceAuthResponse) => void;
  clearSession: () => void;
};

const MarketplaceSessionContext = React.createContext<MarketplaceSessionContextValue | null>(null);

function readStoredUser(): MarketplaceUser | null {
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as MarketplaceUser;
  } catch {
    return null;
  }
}

export const MarketplaceSessionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [token, setToken] = React.useState<string>(() => window.localStorage.getItem(TOKEN_KEY) || "");
  const [user, setUser] = React.useState<MarketplaceUser | null>(() => readStoredUser());

  const setSession = React.useCallback((payload: MarketplaceAuthResponse) => {
    window.localStorage.setItem(TOKEN_KEY, payload.token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(payload.user));
    setToken(payload.token);
    setUser(payload.user);
  }, []);

  const clearSession = React.useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    setToken("");
    setUser(null);
  }, []);

  const value = React.useMemo<MarketplaceSessionContextValue>(
    () => ({
      token,
      user,
      isAuthenticated: Boolean(token && user),
      setSession,
      clearSession,
    }),
    [clearSession, setSession, token, user]
  );

  return <MarketplaceSessionContext.Provider value={value}>{children}</MarketplaceSessionContext.Provider>;
};

export function useMarketplaceSession(): MarketplaceSessionContextValue {
  const value = React.useContext(MarketplaceSessionContext);
  if (!value) {
    throw new Error("useMarketplaceSession must be used within MarketplaceSessionProvider");
  }
  return value;
}
