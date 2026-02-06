import { create } from 'zustand';

interface AuthState {
  user: string | null;
  displayName: string | null;
  snowflakeRole: string | null;
  account: string | null;
  setUser: (
    user: string,
    displayName: string,
    role: string,
    account: string,
  ) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()((set) => ({
  user: null,
  displayName: null,
  snowflakeRole: null,
  account: null,
  setUser: (
    user: string,
    displayName: string,
    role: string,
    account: string,
  ) =>
    set({
      user,
      displayName,
      snowflakeRole: role,
      account,
    }),
  clear: () =>
    set({
      user: null,
      displayName: null,
      snowflakeRole: null,
      account: null,
    }),
}));
