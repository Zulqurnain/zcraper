from django.db import models


class Post(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_PUBLISHED, 'Published')]

    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=500, unique=True, blank=True)
    source_url = models.URLField(max_length=2000)
    price = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    bedrooms = models.CharField(max_length=20, blank=True)
    bathrooms = models.CharField(max_length=20, blank=True)
    floor_size = models.CharField(max_length=50, blank=True)
    location = models.CharField(max_length=500, blank=True)
    property_type = models.CharField(max_length=200, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    image_urls = models.JSONField(default=list, blank=True)       # remote URLs found
    downloaded_images = models.JSONField(default=list, blank=True) # local paths after download
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class ScraperPattern(models.Model):
    """Per-domain learned extraction patterns. Populated automatically by PatternAI."""
    domain      = models.CharField(max_length=255, unique=True, db_index=True)
    title_sel   = models.CharField(max_length=500, blank=True)
    price_sel   = models.CharField(max_length=500, blank=True)
    desc_sel    = models.CharField(max_length=500, blank=True)
    location_sel= models.CharField(max_length=500, blank=True)
    image_sel   = models.CharField(max_length=500, blank=True)
    confidence  = models.FloatField(default=0.0)   # 0.0 – 1.0
    source      = models.CharField(max_length=20, default='auto')  # 'auto' | 'llm'
    scrape_count= models.IntegerField(default=0)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.domain} ({self.confidence:.2f})"
