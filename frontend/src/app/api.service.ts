import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

export interface Product {
  id: string;
  name: string;
  brand: string;
  model: string;
  category: string;
  purchase_date: string | null;
  warranty_expiry_date: string | null;
  description: string | null;
  created_at: string;
}

export interface ProductInput {
  name: string;
  brand: string;
  model: string;
  category: string;
  purchase_date: string | null;
  warranty_expiry_date: string | null;
  description: string | null;
}

export interface DocumentItem {
  id: string;
  product_id: string;
  filename: string;
  document_type: string;
  status: string;
  error_message: string | null;
  suggested_warranty_expiry: string | null;
  chunk_count: number;
  created_at: string;
}

export interface Citation {
  document_id: string;
  filename: string;
  page_number: number | null;
  section: string | null;
  excerpt: string;
}

export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
}

export interface ChatResult {
  conversation_id: string;
  answer: string;
  query_type: string;
  citations: Citation[];
  insufficient_evidence: boolean;
}

export interface ApiHealth {
  status: string;
  llm_provider: string;
  generation_available: boolean;
}

export interface AuthUser {
  id: string;
  email: string;
  created_at: string;
}

interface AuthResponse {
  access_token: string;
  token_type: 'bearer';
  user: AuthUser;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly baseUrl = signal(
    localStorage.getItem('myproductcare.apiUrl') ?? 'http://localhost:8000',
  );
  readonly token = signal(
    localStorage.getItem('myproductcare.session') ??
      localStorage.getItem('myproductcare.token') ??
      '',
  );

  constructor(private readonly http: HttpClient) {}

  setApiUrl(apiUrl: string): void {
    const normalized = apiUrl.trim().replace(/\/+$/, '');
    if (!/^https?:\/\/.+/.test(normalized)) {
      throw new Error('Enter a complete API URL beginning with http:// or https://.');
    }
    this.baseUrl.set(normalized);
    localStorage.setItem('myproductcare.apiUrl', normalized);
  }

  saveSession(token: string): void {
    this.token.set(token);
    localStorage.setItem('myproductcare.session', token);
    localStorage.removeItem('myproductcare.token');
  }

  clearSession(): void {
    this.token.set('');
    localStorage.removeItem('myproductcare.session');
    localStorage.removeItem('myproductcare.token');
  }

  private headers(): HttpHeaders {
    const token = this.token();
    return new HttpHeaders(token ? { Authorization: `Bearer ${token}` } : {});
  }

  private async send<T>(
    method: string,
    path: string,
    body?: unknown,
    headers = this.headers(),
  ): Promise<T> {
    try {
      return await firstValueFrom(
        this.http.request<T>(method, `${this.baseUrl()}${path}`, {
          body,
          headers,
        }),
      );
    } catch (error: unknown) {
      if (error instanceof HttpErrorResponse) {
        const detail =
          typeof error.error?.detail === 'string'
            ? error.error.detail
            : error.message || 'The API request failed.';
        throw new Error(`API ${error.status || 'connection'}: ${detail}`);
      }
      throw error;
    }
  }

  listProducts(): Promise<Product[]> {
    return this.send<Product[]>('GET', '/api/products');
  }

  health(): Promise<ApiHealth> {
    return this.send<ApiHealth>('GET', '/api/health');
  }

  register(email: string, password: string): Promise<AuthResponse> {
    return this.send<AuthResponse>(
      'POST',
      '/api/auth/register',
      { email, password },
      new HttpHeaders(),
    );
  }

  login(email: string, password: string): Promise<AuthResponse> {
    return this.send<AuthResponse>(
      'POST',
      '/api/auth/login',
      { email, password },
      new HttpHeaders(),
    );
  }

  currentUser(): Promise<AuthUser> {
    return this.send<AuthUser>('GET', '/api/auth/me');
  }

  logout(): Promise<void> {
    return this.send<void>('POST', '/api/auth/logout');
  }

  createProduct(payload: ProductInput): Promise<Product> {
    return this.send<Product>('POST', '/api/products', payload);
  }

  updateProduct(id: string, payload: ProductInput): Promise<Product> {
    return this.send<Product>('PUT', `/api/products/${encodeURIComponent(id)}`, payload);
  }

  deleteProduct(id: string): Promise<void> {
    return this.send<void>('DELETE', `/api/products/${encodeURIComponent(id)}`);
  }

  listDocuments(productId: string): Promise<DocumentItem[]> {
    return this.send<DocumentItem[]>(
      'GET',
      `/api/products/${encodeURIComponent(productId)}/documents`,
    );
  }

  uploadDocument(productId: string, file: File, documentType: string): Promise<DocumentItem> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('document_type', documentType);
    return this.send<DocumentItem>(
      'POST',
      `/api/products/${encodeURIComponent(productId)}/documents`,
      form,
    );
  }

  deleteDocument(id: string): Promise<void> {
    return this.send<void>('DELETE', `/api/documents/${encodeURIComponent(id)}`);
  }

  ask(
    productId: string,
    question: string,
    conversationId: string | null,
  ): Promise<ChatResult> {
    return this.send<ChatResult>('POST', '/api/chat', {
      product_id: productId,
      question,
      conversation_id: conversationId,
    });
  }
}
