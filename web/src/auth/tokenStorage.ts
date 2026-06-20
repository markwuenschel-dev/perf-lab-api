const TOKEN_KEY = "perf_lab_access_token";
const EMAIL_KEY = "perf_lab_session_email";

export function getStoredToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(EMAIL_KEY);
}

export function getStoredEmail(): string | null {
  return sessionStorage.getItem(EMAIL_KEY);
}

export function setStoredEmail(email: string): void {
  sessionStorage.setItem(EMAIL_KEY, email);
}
