/**
 * DeDox Main Application JavaScript
 */

// Global app state
function app() {
    return {
        // Auth state
        isAuthenticated: false,
        user: null,
        token: null,
        
        // UI state
        currentPage: 'dashboard',
        darkMode: localStorage.getItem('darkMode') === 'true',
        loading: false,
        loadingMessage: '',

        // Initialize
        async init() {
            // Check for stored auth
            this.token = localStorage.getItem('token');
            if (this.token) {
                await this.fetchUser();
            } else {
                // Redirect to login if not authenticated
                if (!window.location.pathname.includes('/login')) {
                    window.location.href = '/login';
                }
            }
            
            // Set current page based on URL
            this.setCurrentPage();
            
            // Watch dark mode
            this.$watch('darkMode', (value) => {
                localStorage.setItem('darkMode', value);
            });
        },
        
        setCurrentPage() {
            const path = window.location.pathname;
            if (path === '/' || path === '/dashboard') {
                this.currentPage = 'dashboard';
            } else if (path.startsWith('/scan')) {
                this.currentPage = 'scan';
            } else if (path.startsWith('/documents')) {
                this.currentPage = 'documents';
            } else if (path.startsWith('/review')) {
                this.currentPage = 'review';
            } else if (path.startsWith('/settings')) {
                this.currentPage = 'settings';
            }
        },
        
        async fetchUser() {
            try {
                const response = await api.get('/api/auth/me');
                this.user = response;
                this.isAuthenticated = true;
            } catch (error) {
                console.error('Failed to fetch user:', error);
                this.logout();
            }
        },

        logout() {
            localStorage.removeItem('token');
            this.token = null;
            this.user = null;
            this.isAuthenticated = false;
            window.location.href = '/login';
        },
        
        showLoading(message = 'Loading...') {
            this.loading = true;
            this.loadingMessage = message;
        },
        
        hideLoading() {
            this.loading = false;
            this.loadingMessage = '';
        }
    };
}

// Toast notifications store
function toastStore() {
    return {
        toasts: [],
        
        add(message, type = 'info', duration = 5000) {
            const id = Date.now();
            this.toasts.push({ id, message, type, show: true });
            
            setTimeout(() => {
                this.remove(id);
            }, duration);
        },
        
        remove(id) {
            const index = this.toasts.findIndex(t => t.id === id);
            if (index !== -1) {
                this.toasts[index].show = false;
                setTimeout(() => {
                    this.toasts = this.toasts.filter(t => t.id !== id);
                }, 200);
            }
        },
        
        success(message) {
            this.add(message, 'success');
        },
        
        error(message) {
            this.add(message, 'error');
        },
        
        warning(message) {
            this.add(message, 'warning');
        },
        
        info(message) {
            this.add(message, 'info');
        }
    };
}

// Global toast instance
window.toast = {
    _store: null,
    
    init(store) {
        this._store = store;
    },
    
    success(message) {
        this._store?.success(message);
    },
    
    error(message) {
        this._store?.error(message);
    },
    
    warning(message) {
        this._store?.warning(message);
    },
    
    info(message) {
        this._store?.info(message);
    }
};

// Format helpers
const formatters = {
    date(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        return date.toLocaleDateString('de-DE', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    },
    
    datetime(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        return date.toLocaleDateString('de-DE', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    },
    
    relativeTime(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHour = Math.floor(diffMin / 60);
        const diffDay = Math.floor(diffHour / 24);
        
        if (diffSec < 60) return 'Just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHour < 24) return `${diffHour}h ago`;
        if (diffDay < 7) return `${diffDay}d ago`;
        return this.date(dateString);
    },
    
    fileSize(bytes) {
        if (!bytes) return '-';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return `${size.toFixed(1)} ${units[unitIndex]}`;
    },
    
    percentage(value) {
        if (value === null || value === undefined) return '-';
        return `${Math.round(value * 100)}%`;
    },
    
    currency(amount, currency = 'EUR') {
        if (amount === null || amount === undefined) return '-';
        return new Intl.NumberFormat('de-DE', {
            style: 'currency',
            currency: currency
        }).format(amount);
    }
};

// Make formatters globally available
window.fmt = formatters;
