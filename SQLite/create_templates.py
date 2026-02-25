# Product List Template (templates/products_list.html)

PRODUCTS_LIST_HTML = """{% extends "base.html" %}

{% block title %}Products - Anson Product Encoder{% endblock %}

{% block content %}
<h2>Products ({{ "{:,}".format(total) }} total)</h2>

<div class="card">
    <form method="get" style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px;">
        <select name="filter" onchange="this.form.submit()" style="width: auto;">
            <option value="all" {% if filter_type == 'all' %}selected{% endif %}>All Products</option>
            <option value="needs_work" {% if filter_type == 'needs_work' %}selected{% endif %}>Needs Work</option>
            <option value="COMPLETE" {% if filter_type == 'COMPLETE' %}selected{% endif %}>Complete</option>
            <option value="NEEDS_DESCRIPTION" {% if filter_type == 'NEEDS_DESCRIPTION' %}selected{% endif %}>Needs Description</option>
            <option value="NEEDS_NAME" {% if filter_type == 'NEEDS_NAME' %}selected{% endif %}>Needs Name</option>
            <option value="NEEDS_BRAND" {% if filter_type == 'NEEDS_BRAND' %}selected{% endif %}>Needs Brand</option>
        </select>
        
        <input type="text" name="search" placeholder="Search MERKEY or description..." value="{{ search }}" style="flex: 1; min-width: 200px;">
        <button type="submit" class="btn btn-primary">Search</button>
    </form>
    
    <div style="overflow-x: auto;">
        <table>
            <thead>
                <tr>
                    <th>MERKEY</th>
                    <th>Description</th>
                    <th>Name</th>
                    <th>Brand</th>
                    <th>Status</th>
                    <th>Sales (24M)</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {% for product in products %}
                <tr>
                    <td><code>{{ product.merkey }}</code></td>
                    <td>{{ product.description[:60] }}{% if product.description|length > 60 %}...{% endif %}</td>
                    <td>{{ product.name or '-' }}</td>
                    <td>{{ product.brand or '-' }}</td>
                    <td>
                        {% if product.data_quality == 'COMPLETE' %}
                            <span class="badge badge-success">Complete</span>
                        {% else %}
                            <span class="badge badge-warning">{{ product.data_quality }}</span>
                        {% endif %}
                    </td>
                    <td>{{ "{:,}".format(product.txn_count_24m) if product.txn_count_24m else '-' }}</td>
                    <td>
                        <a href="{{ url_for('product_edit', merkey=product.merkey) }}" class="btn btn-primary btn-sm">Edit</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    {% if total_pages > 1 %}
    <div class="pagination">
        {% if page > 1 %}
        <a href="{{ url_for('products_list', page=page-1, filter=filter_type, search=search) }}">&laquo; Previous</a>
        {% endif %}
        
        {% for p in range(1, total_pages + 1) %}
            {% if p == page %}
                <a href="#" class="active">{{ p }}</a>
            {% elif p == 1 or p == total_pages or (p >= page - 2 and p <= page + 2) %}
                <a href="{{ url_for('products_list', page=p, filter=filter_type, search=search) }}">{{ p }}</a>
            {% elif p == page - 3 or p == page + 3 %}
                <span>...</span>
            {% endif %}
        {% endfor %}
        
        {% if page < total_pages %}
        <a href="{{ url_for('products_list', page=page+1, filter=filter_type, search=search) }}">Next &raquo;</a>
        {% endif %}
    </div>
    {% endif %}
</div>
{% endblock %}
"""

# Product Edit Template (templates/product_edit.html)

PRODUCT_EDIT_HTML = """{% extends "base.html" %}

{% block title %}Edit Product {{ product.merkey }} - Anson Product Encoder{% endblock %}

{% block extra_css %}
<style>
    .product-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
    }
    
    .product-image {
        max-width: 200px;
        border-radius: 8px;
        margin-bottom: 20px;
    }
    
    .sales-info {
        background: #f8f9fa;
        padding: 15px;
        border-radius: 4px;
        margin-bottom: 20px;
    }
    
    .sales-info strong {
        display: inline-block;
        width: 150px;
    }
    
    .barcode-list {
        list-style: none;
        padding: 0;
    }
    
    .barcode-list li {
        padding: 5px 0;
        font-family: monospace;
    }
    
    .form-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 20px;
    }
    
    @media (max-width: 768px) {
        .form-row {
            grid-template-columns: 1fr;
        }
    }
    
    .required {
        color: #E31E24;
    }
    
    .help-text {
        font-size: 12px;
        color: #666;
        margin-top: 5px;
    }
</style>
{% endblock %}

{% block content %}
<div class="product-header">
    <h2>Edit Product: {{ product.merkey }}</h2>
    <a href="{{ url_for('products_list') }}" class="btn btn-secondary">← Back to List</a>
</div>

<div class="card">
    <div class="sales-info">
        <h3>Product Performance</h3>
        <p><strong>Sales (24M):</strong> {{ "{:,}".format(product.txn_count_24m) if product.txn_count_24m else 'No sales data' }}</p>
        <p><strong>Last Sale:</strong> {{ product.last_sale_date or 'N/A' }}</p>
        <p><strong>Velocity Score:</strong> {{ "%.2f"|format(product.velocity_score) if product.velocity_score else 'N/A' }}</p>
        <p><strong>Priority:</strong> {{ product.priority or 'N/A' }}</p>
        <p><strong>Current Status:</strong> 
            {% if product.data_quality == 'COMPLETE' %}
                <span class="badge badge-success">Complete ✓</span>
            {% else %}
                <span class="badge badge-warning">{{ product.data_quality }}</span>
            {% endif %}
        </p>
    </div>
    
    {% if product.image_url %}
    <img src="{{ product.image_url }}" alt="{{ product.description }}" class="product-image">
    {% endif %}
    
    <form method="post" action="{{ url_for('product_update', merkey=product.merkey) }}" id="productForm">
        <h3>Product Information</h3>
        
        <div class="form-group">
            <label for="description">Description <span class="required">*</span></label>
            <textarea name="description" id="description" required>{{ product.description }}</textarea>
            <div class="help-text">Customer-friendly product description. Make it clear and appealing!</div>
        </div>
        
        <div class="form-row">
            <div class="form-group">
                <label for="name">Product Name <span class="required">*</span></label>
                <input type="text" name="name" id="name" value="{{ product.name or '' }}" required>
                <div class="help-text">Short product name (e.g., "Classic White Bread")</div>
            </div>
            
            <div class="form-group">
                <label for="brand">Brand</label>
                <input type="text" name="brand" id="brand" value="{{ product.brand_name or '' }}" list="brandsList" autocomplete="off">
                <datalist id="brandsList">
                    {% for brand in brands %}
                    <option value="{{ brand.name }}">
                    {% endfor %}
                </datalist>
                <div class="help-text">Start typing to see suggestions</div>
            </div>
        </div>
        
        <div class="form-row">
            <div class="form-group">
                <label for="category_id">Category</label>
                <select name="category_id" id="category_id">
                    <option value="">-- Select Category --</option>
                    {% for category in categories %}
                    <option value="{{ category.id }}" {% if product.category_id == category.id %}selected{% endif %}>
                        {{ category.name }}
                    </option>
                    {% endfor %}
                </select>
            </div>
            
            <div class="form-group">
                <label for="department_id">Department</label>
                <select name="department_id" id="department_id">
                    <option value="">-- Select Department --</option>
                    {% for department in departments %}
                    <option value="{{ department.id }}" {% if product.department_id == department.id %}selected{% endif %}>
                        {{ department.name }}
                    </option>
                    {% endfor %}
                </select>
            </div>
        </div>
        
        <div class="form-row">
            <div class="form-group">
                <label for="size">Size</label>
                <input type="text" name="size" id="size" value="{{ product.size or '' }}" placeholder="e.g., 600 g, 1 L, 250 ml">
                <div class="help-text">Use proper spacing and lowercase units</div>
            </div>
            
            <div class="form-group">
                <label for="weight_volume">Weight/Volume</label>
                <input type="text" name="weight_volume" id="weight_volume" value="{{ product.weight_volume or '' }}">
            </div>
        </div>
        
        <div class="form-group">
            <label for="unit">Unit of Measurement</label>
            <input type="text" name="unit" id="unit" value="{{ product.unit_of_measurement or '' }}" placeholder="g, kg, ml, L, pcs">
        </div>
        
        <div class="form-group">
            <label for="notes">Encoder Notes (Optional)</label>
            <textarea name="notes" id="notes" style="min-height: 60px;"></textarea>
            <div class="help-text">Any notes about this update</div>
        </div>
        
        {% if barcodes %}
        <div class="form-group">
            <label>Barcodes:</label>
            <ul class="barcode-list">
                {% for barcode in barcodes %}
                <li>
                    {{ barcode.barcode }}
                    {% if barcode.is_primary %}<span class="badge badge-success">Primary</span>{% endif %}
                </li>
                {% endfor %}
            </ul>
        </div>
        {% endif %}
        
        <div style="display: flex; gap: 10px; margin-top: 30px;">
            <button type="submit" class="btn btn-success">💾 Save Changes</button>
            <button type="button" onclick="location.href='{{ url_for('products_list') }}'" class="btn btn-secondary">Cancel</button>
        </div>
    </form>
</div>

<div class="card" style="margin-top: 20px;">
    <h3>Current Data (from MEDESC)</h3>
    <p><strong>Original Description:</strong> {{ product.description }}</p>
    <p><strong>Enrichment Notes:</strong> {{ product.enrichment_notes or 'None' }}</p>
</div>
{% endblock %}

{% block extra_js %}
<script>
// Form validation
document.getElementById('productForm').addEventListener('submit', function(e) {
    const description = document.getElementById('description').value.trim();
    const name = document.getElementById('name').value.trim();
    
    if (!description || !name) {
        e.preventDefault();
        alert('Description and Name are required!');
        return false;
    }
    
    // Show loading state
    const btn = this.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = '💾 Saving...';
});

// Brand autocomplete (simple implementation)
const brandInput = document.getElementById('brand');
brandInput.addEventListener('input', function() {
    // The datalist handles this, but you could add AJAX here
});
</script>
{% endblock %}
"""

# Write the templates to files
import os
os.makedirs('templates', exist_ok=True)

with open('templates/products_list.html', 'w', encoding='utf-8') as f:
    f.write(PRODUCTS_LIST_HTML)

with open('templates/product_edit.html', 'w', encoding='utf-8') as f:
    f.write(PRODUCT_EDIT_HTML)

print("✓ Templates created successfully!")
