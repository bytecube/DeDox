/**
 * DeDox API Client
 */

const api = {
    baseUrl: '',
    
    getToken() {
        return localStorage.getItem('token');
    },
    
    setToken(token) {
        localStorage.setItem('token', token);
    },
    
    async request(method, endpoint, data = null, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const token = this.getToken();
        
        const headers = {
            ...options.headers
        };
        
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
        
        if (data && !(data instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
        }
        
        const config = {
            method,
            headers,
            ...options
        };
        
        if (data) {
            config.body = data instanceof FormData ? data : JSON.stringify(data);
        }
        
        try {
            const response = await fetch(url, config);
            
            // Handle 401 - Unauthorized
            if (response.status === 401) {
                localStorage.removeItem('token');
                window.location.href = '/login';
                throw new Error('Unauthorized');
            }
            
            // Handle other errors
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || errorData.detail || `HTTP ${response.status}`);
            }
            
            // Return JSON if available
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                return await response.json();
            }
            
            return response;
        } catch (error) {
            console.error(`API Error [${method} ${endpoint}]:`, error);
            throw error;
        }
    },
    
    get(endpoint, options = {}) {
        return this.request('GET', endpoint, null, options);
    },
    
    post(endpoint, data, options = {}) {
        return this.request('POST', endpoint, data, options);
    },
    
    put(endpoint, data, options = {}) {
        return this.request('PUT', endpoint, data, options);
    },
    
    patch(endpoint, data, options = {}) {
        return this.request('PATCH', endpoint, data, options);
    },
    
    delete(endpoint, options = {}) {
        return this.request('DELETE', endpoint, null, options);
    },
    
    // Auth endpoints
    auth: {
        async login(username, password) {
            const response = await api.post('/api/auth/login', { username, password });
            api.setToken(response.access_token);
            return response;
        },
        
        async register(username, email, password) {
            return api.post('/api/auth/register', { username, email, password });
        },
        
        async me() {
            return api.get('/api/auth/me');
        },
        
        logout() {
            localStorage.removeItem('token');
            window.location.href = '/login';
        }
    },
    
    // Documents endpoints
    documents: {
        async list(params = {}) {
            const query = new URLSearchParams(params).toString();
            return api.get(`/api/documents${query ? '?' + query : ''}`);
        },
        
        async get(id) {
            return api.get(`/api/documents/${id}`);
        },
        
        async getMetadata(id) {
            return api.get(`/api/documents/${id}/metadata`);
        },
        
        async updateMetadata(id, metadata) {
            return api.put(`/api/documents/${id}/metadata`, metadata);
        },
        
        async upload(file, onProgress) {
            const formData = new FormData();
            formData.append('file', file);
            
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable && onProgress) {
                        onProgress(Math.round((e.loaded / e.total) * 100));
                    }
                });
                
                xhr.addEventListener('load', () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        resolve(JSON.parse(xhr.responseText));
                    } else {
                        reject(new Error(`Upload failed: ${xhr.status}`));
                    }
                });
                
                xhr.addEventListener('error', () => reject(new Error('Upload failed')));
                
                xhr.open('POST', '/api/documents/upload');
                const token = api.getToken();
                if (token) {
                    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
                }
                xhr.send(formData);
            });
        },
        
        async uploadBatch(files, onProgress) {
            const formData = new FormData();
            files.forEach(file => formData.append('files', file));
            return api.post('/api/documents/upload/batch', formData);
        },
        
        async delete(id) {
            return api.delete(`/api/documents/${id}`);
        },
        
        async reprocess(id) {
            return api.post(`/api/documents/${id}/reprocess`);
        },
        
        async getJob(id) {
            return api.get(`/api/documents/${id}/job`);
        }
    },
    
    // Jobs endpoints
    jobs: {
        async list(params = {}) {
            const query = new URLSearchParams(params).toString();
            return api.get(`/api/jobs${query ? '?' + query : ''}`);
        },

        async get(id) {
            return api.get(`/api/jobs/${id}`);
        },

        async getProgress(id) {
            return api.get(`/api/jobs/${id}/progress`);
        },

        async cancel(id) {
            return api.post(`/api/jobs/${id}/cancel`);
        },

        async retry(id) {
            return api.post(`/api/jobs/${id}/retry`);
        },

        async getStats() {
            return api.get('/api/jobs/stats/summary');
        },

        async getLogs(id) {
            return api.get(`/api/jobs/${id}/logs`);
        }
    },
    
    // Search endpoints
    search: {
        async query(q, params = {}) {
            const allParams = { q, ...params };
            const query = new URLSearchParams(allParams).toString();
            return api.get(`/api/search?${query}`);
        },
        
        async byMetadata(params = {}) {
            const query = new URLSearchParams(params).toString();
            return api.get(`/api/search/metadata?${query}`);
        },
        
        async recent(limit = 10) {
            return api.get(`/api/search/recent?limit=${limit}`);
        },

        async similar(documentId, limit = 5) {
            return api.get(`/api/search/similar/${documentId}?limit=${limit}`);
        }
    },
    
    // Config endpoints
    config: {
        async getMetadataFields() {
            return api.get('/api/config/metadata-fields');
        },

        async getDocumentTypes() {
            return api.get('/api/config/document-types');
        },

        async getSettings() {
            return api.get('/api/config/settings');
        },

        async getStatus() {
            return api.get('/api/config/status');
        },

        async get() {
            return api.get('/api/config/settings/full');
        },

        async update(settings) {
            return api.put('/api/config/settings', settings);
        },

        async testPaperless() {
            return api.post('/api/config/test-paperless');
        },

        async testOllama() {
            return api.post('/api/config/test-ollama');
        }
    },

    // Extraction Fields endpoints
    extractionFields: {
        async list(enabledOnly = false) {
            const params = enabledOnly ? '?enabled_only=true' : '';
            return api.get(`/api/config/extraction-fields${params}`);
        },

        async get(id) {
            return api.get(`/api/config/extraction-fields/${id}`);
        },

        async create(fieldData) {
            return api.post('/api/config/extraction-fields', fieldData);
        },

        async update(id, fieldData) {
            return api.put(`/api/config/extraction-fields/${id}`, fieldData);
        },

        async delete(id) {
            return api.delete(`/api/config/extraction-fields/${id}`);
        },

        async reorder(fieldOrders) {
            return api.post('/api/config/extraction-fields/reorder', fieldOrders);
        },

        async test(testData) {
            return api.post('/api/config/extraction-fields/test', testData);
        }
    }
};

// Make API globally available
window.api = api;
