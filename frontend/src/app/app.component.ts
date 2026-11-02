import { CommonModule } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  ApiService,
  ChatTurn,
  DocumentItem,
  Product,
  ProductInput,
} from './api.service';

type View = 'overview' | 'products' | 'documents' | 'assistant';
type WarrantyLevel = 'expired' | 'soon' | 'active' | 'unknown';

interface WarrantyStatus {
  label: string;
  level: WarrantyLevel;
}

@Component({
  selector: 'mpc-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
})
export class AppComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly products = signal<Product[]>([]);
  readonly documents = signal<DocumentItem[]>([]);
  readonly turns = signal<ChatTurn[]>([]);
  readonly view = signal<View>('overview');
  readonly selectedProductId = signal('');
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly error = signal('');
  readonly authMode = signal<'register' | 'login'>('register');
  readonly email = signal('');
  readonly password = signal('');
  readonly accountEmail = signal('');
  readonly isAuthenticated = signal(false);
  readonly productName = signal('');
  readonly brand = signal('');
  readonly model = signal('');
  readonly category = signal('');
  readonly purchaseDate = signal('');
  readonly warrantyExpiryDate = signal('');
  readonly description = signal('');
  readonly question = signal('');
  readonly conversationId = signal<string | null>(null);
  readonly documentType = signal('user_manual');
  readonly selectedFile = signal<File | null>(null);
  readonly connectionConfigured = signal(false);
  readonly generationAvailable = signal<boolean | null>(null);
  readonly generationProvider = signal('openai');

  ngOnInit(): void {
    if (this.api.token()) {
      void this.restoreSession();
    } else {
      this.connectionConfigured.set(true);
    }
  }

  async restoreSession(): Promise<void> {
    try {
      const user = await this.api.currentUser();
      this.accountEmail.set(user.email);
      this.isAuthenticated.set(true);
      this.connectionConfigured.set(false);
      await Promise.all([this.loadHealth(), this.loadProducts()]);
    } catch (error: unknown) {
      this.api.clearSession();
      this.isAuthenticated.set(false);
      this.connectionConfigured.set(true);
      if (!(error instanceof Error && error.message.includes('API 401:'))) {
        this.showError(error);
      }
    }
  }

  async loadHealth(): Promise<void> {
    try {
      const health = await this.api.health();
      this.generationAvailable.set(health.generation_available);
      this.generationProvider.set(health.llm_provider);
    } catch (error: unknown) {
      this.showError(error);
    }
  }

  async authenticate(): Promise<void> {
    this.error.set('');
    this.saving.set(true);
    try {
      const response =
        this.authMode() === 'register'
          ? await this.api.register(this.email(), this.password())
          : await this.api.login(this.email(), this.password());
      this.api.saveSession(response.access_token);
      this.accountEmail.set(response.user.email);
      this.isAuthenticated.set(true);
      this.password.set('');
      this.connectionConfigured.set(false);
      await Promise.all([this.loadHealth(), this.loadProducts()]);
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.saving.set(false);
    }
  }

  async signOut(): Promise<void> {
    this.error.set('');
    try {
      await this.api.logout();
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.api.clearSession();
      this.isAuthenticated.set(false);
      this.accountEmail.set('');
      this.products.set([]);
      this.documents.set([]);
      this.turns.set([]);
      this.selectedProductId.set('');
      this.conversationId.set(null);
      this.connectionConfigured.set(true);
    }
  }

  async loadProducts(): Promise<void> {
    this.loading.set(true);
    this.error.set('');
    try {
      const products = await this.api.listProducts();
      this.products.set(products);
      if (
        !products.some((product) => product.id === this.selectedProductId()) &&
        products.length > 0
      ) {
        this.selectProduct(products[0].id);
      }
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.loading.set(false);
    }
  }

  async createProduct(): Promise<void> {
    const payload: ProductInput = {
      name: this.productName().trim(),
      brand: this.brand().trim(),
      model: this.model().trim(),
      category: this.category().trim(),
      purchase_date: this.purchaseDate() || null,
      warranty_expiry_date: this.warrantyExpiryDate() || null,
      description: this.description().trim() || null,
    };
    if (!payload.name || !payload.brand || !payload.model || !payload.category) {
      this.error.set('Add a name, brand, model, and category to register this product.');
      return;
    }
    this.saving.set(true);
    this.error.set('');
    try {
      const product = await this.api.createProduct(payload);
      this.products.update((products) => [product, ...products]);
      this.selectProduct(product.id);
      this.resetProductForm();
      this.view.set('documents');
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.saving.set(false);
    }
  }

  async deleteProduct(product: Product): Promise<void> {
    if (!window.confirm(`Delete ${product.name} and all its documents?`)) {
      return;
    }
    this.error.set('');
    try {
      await this.api.deleteProduct(product.id);
      this.products.update((products) => products.filter((item) => item.id !== product.id));
      if (this.selectedProductId() === product.id) {
        this.selectedProductId.set('');
        this.documents.set([]);
        this.turns.set([]);
      }
      await this.loadProducts();
    } catch (error: unknown) {
      this.showError(error);
    }
  }

  selectProduct(productId: string): void {
    const changed = productId !== this.selectedProductId();
    this.selectedProductId.set(productId);
    this.warrantyExpiryDate.set(
      this.products().find((product) => product.id === productId)?.warranty_expiry_date ?? '',
    );
    if (changed) {
      this.turns.set([]);
      this.conversationId.set(null);
    }
    void this.loadDocuments();
  }

  selectedProduct(): Product | undefined {
    return this.products().find((product) => product.id === this.selectedProductId());
  }

  warrantyStatus(product: Product): WarrantyStatus {
    if (!product.warranty_expiry_date) {
      return { label: 'Expiry date not set', level: 'unknown' };
    }
    const [year, month, day] = product.warranty_expiry_date.split('-').map(Number);
    const expiry = Date.UTC(year, month - 1, day);
    const todayDate = new Date();
    const today = Date.UTC(
      todayDate.getFullYear(),
      todayDate.getMonth(),
      todayDate.getDate(),
    );
    const daysRemaining = Math.round((expiry - today) / 86_400_000);
    if (daysRemaining < 0) {
      return {
        label: `Expired ${Math.abs(daysRemaining)} day${Math.abs(daysRemaining) === 1 ? '' : 's'} ago`,
        level: 'expired',
      };
    }
    if (daysRemaining === 0) {
      return { label: 'Expires today', level: 'soon' };
    }
    if (daysRemaining <= 30) {
      return { label: `Expires in ${daysRemaining} days`, level: 'soon' };
    }
    return { label: `Expires ${this.formatWarrantyDate(product.warranty_expiry_date)}`, level: 'active' };
  }

  warrantyAttentionProducts(): Product[] {
    return this.products().filter((product) => {
      const level = this.warrantyStatus(product).level;
      return level === 'expired' || level === 'soon';
    });
  }

  useWarrantySuggestion(document: DocumentItem): void {
    if (document.suggested_warranty_expiry) {
      this.warrantyExpiryDate.set(document.suggested_warranty_expiry);
    }
  }

  async saveWarrantyExpiry(): Promise<void> {
    const product = this.selectedProduct();
    if (!product) {
      return;
    }
    const payload: ProductInput = {
      name: product.name,
      brand: product.brand,
      model: product.model,
      category: product.category,
      purchase_date: product.purchase_date,
      warranty_expiry_date: this.warrantyExpiryDate() || null,
      description: product.description,
    };
    this.saving.set(true);
    this.error.set('');
    try {
      const updated = await this.api.updateProduct(product.id, payload);
      this.products.update((products) =>
        products.map((item) => (item.id === updated.id ? updated : item)),
      );
      this.warrantyExpiryDate.set(updated.warranty_expiry_date ?? '');
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.saving.set(false);
    }
  }

  async loadDocuments(): Promise<void> {
    const productId = this.selectedProductId();
    if (!productId) {
      this.documents.set([]);
      return;
    }
    try {
      this.documents.set(await this.api.listDocuments(productId));
    } catch (error: unknown) {
      this.showError(error);
    }
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile.set(input.files?.[0] ?? null);
  }

  async uploadDocument(): Promise<void> {
    const file = this.selectedFile();
    const productId = this.selectedProductId();
    if (!file || !productId) {
      this.error.set('Choose a product and a document first.');
      return;
    }
    this.saving.set(true);
    this.error.set('');
    try {
      await this.api.uploadDocument(productId, file, this.documentType());
      this.selectedFile.set(null);
      await this.loadDocuments();
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.saving.set(false);
    }
  }

  async deleteDocument(document: DocumentItem): Promise<void> {
    this.error.set('');
    try {
      await this.api.deleteDocument(document.id);
      await this.loadDocuments();
    } catch (error: unknown) {
      this.showError(error);
    }
  }

  async ask(): Promise<void> {
    const question = this.question().trim();
    const productId = this.selectedProductId();
    if (!question || !productId) {
      return;
    }
    this.turns.update((turns) => [...turns, { role: 'user', content: question }]);
    this.question.set('');
    this.saving.set(true);
    this.error.set('');
    try {
      const result = await this.api.ask(productId, question, this.conversationId());
      this.conversationId.set(result.conversation_id);
      this.turns.update((turns) => [
        ...turns,
        { role: 'assistant', content: result.answer, citations: result.citations },
      ]);
    } catch (error: unknown) {
      this.showError(error);
    } finally {
      this.saving.set(false);
    }
  }

  newConversation(): void {
    this.conversationId.set(null);
    this.turns.set([]);
  }

  open(view: View): void {
    this.view.set(view);
    if (view === 'documents' && this.selectedProductId()) {
      void this.loadDocuments();
    }
  }

  productCountLabel(): string {
    return this.products().length === 1 ? 'registered product' : 'registered products';
  }

  private resetProductForm(): void {
    this.productName.set('');
    this.brand.set('');
    this.model.set('');
    this.category.set('');
    this.purchaseDate.set('');
    this.warrantyExpiryDate.set('');
    this.description.set('');
  }

  formatWarrantyDate(value: string): string {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(year, month - 1, day).toLocaleDateString();
  }

  private showError(error: unknown): void {
    this.error.set(error instanceof Error ? error.message : 'Something went wrong. Please retry.');
  }
}
