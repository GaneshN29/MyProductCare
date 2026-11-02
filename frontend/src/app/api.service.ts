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
  description: string | null;
  created_at: string;
}

export interface ProductInput {
  name: string;
  brand: string;
  model: string;
  category: string;
  purchase_date: string | null;
  description: string | null;
}

export interface DocumentItem {
  id: string;
  product_id: string;
  filename: string;
  document_type: string;
  status: string;
  error_message: string | null;
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
  generation_available: boolean;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly baseUrl =
    localStorage.getItem('myproductcare.apiUrl') ?? 'http://localhost:8000';
  readonly token = signal(localStorage.getItem('myproductcare.token') ?? '');

  constructor(private readonly http: HttpClient) {}

  saveCredentials(apiUrl: string, token: string): void {
    const normalized = apiUrl.trim().replace(/\/+$/, '');
    if (!/^https?:\/\/.+/.test(normalized)) {
      throw new Error('Enter a complete API URL beginning with http:// or https://.');
    }
    this.token.set(token.trim());
    localStorage.setItem('myproductcare.apiUrl', normalized);
    localStorage.setItem('myproductcare.token', token.trim());
    window.location.reload();
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
        this.http.request<T>(method, `${this.baseUrl}${path}`, {
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

  createProduct(payload: ProductInput): Promise<Product> {
    return this.send<Product>('POST', '/api/products', payload);
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
