"""
Management command to test DALL-E API connectivity and drawing generation
Usage: python manage.py test_dalle
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from decouple import config
from openai import OpenAI
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Test OpenAI DALL-E API connectivity and configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--generate-test',
            action='store_true',
            help='Generate a test P&ID drawing (costs API credits)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('   DALL-E API Configuration & Test'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))

        # Check API Key
        api_key = config('OPENAI_API_KEY', default='')
        
        if not api_key or api_key == '':
            self.stdout.write(self.style.ERROR('❌ OpenAI API Key: NOT CONFIGURED'))
            self.stdout.write(self.style.WARNING('\nTo configure:'))
            self.stdout.write('   1. Add to .env file: OPENAI_API_KEY=sk-...')
            self.stdout.write('   2. Or set environment variable: export OPENAI_API_KEY=sk-...')
            self.stdout.write('   3. Get API key from: https://platform.openai.com/api-keys\n')
            return
        
        if api_key.startswith('your-'):
            self.stdout.write(self.style.ERROR('❌ OpenAI API Key: PLACEHOLDER VALUE'))
            self.stdout.write(self.style.WARNING('\nPlease replace with actual API key from https://platform.openai.com/api-keys\n'))
            return
        
        # Show masked key
        masked_key = api_key[:7] + '...' + api_key[-4:] if len(api_key) > 11 else '***'
        self.stdout.write(self.style.SUCCESS(f'✅ OpenAI API Key: {masked_key}'))
        
        # Test API connectivity
        self.stdout.write('\n📡 Testing API connectivity...')
        try:
            client = OpenAI(api_key=api_key)
            
            # Try to list models to verify API key
            models = client.models.list()
            self.stdout.write(self.style.SUCCESS('✅ API Connection: SUCCESS'))
            
            # Check available models
            model_names = [model.id for model in models.data]
            has_dalle3 = any('dall-e-3' in m for m in model_names)
            has_dalle2 = any('dall-e-2' in m for m in model_names)
            
            if has_dalle3:
                self.stdout.write(self.style.SUCCESS('✅ DALL-E 3: AVAILABLE (HD Quality)'))
            else:
                self.stdout.write(self.style.WARNING('⚠️  DALL-E 3: Not listed (may still work)'))
            
            if has_dalle2:
                self.stdout.write(self.style.SUCCESS('✅ DALL-E 2: AVAILABLE (Fallback)'))
            else:
                self.stdout.write(self.style.WARNING('⚠️  DALL-E 2: Not listed (may still work)'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ API Connection: FAILED'))
            self.stdout.write(self.style.ERROR(f'   Error: {str(e)}'))
            self.stdout.write(self.style.WARNING('\nPossible issues:'))
            self.stdout.write('   1. Invalid API key')
            self.stdout.write('   2. Network connectivity problems')
            self.stdout.write('   3. API key lacks required permissions')
            self.stdout.write('   4. OpenAI API service unavailable\n')
            return
        
        # Configuration summary
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('   Configuration Summary'))
        self.stdout.write('='*70)
        
        from apps.pfd_converter.services import DrawingConfig
        
        self.stdout.write(f'\n🎨 Drawing Configuration:')
        self.stdout.write(f'   • DALL-E 3 Enabled: {DrawingConfig.ENABLE_DALLE3}')
        self.stdout.write(f'   • DALL-E 2 Fallback: {DrawingConfig.ENABLE_DALLE2_FALLBACK}')
        self.stdout.write(f'   • Programmatic Fallback: {DrawingConfig.ENABLE_PROGRAMMATIC_FALLBACK}')
        self.stdout.write(f'   • DALL-E 3 Size: {DrawingConfig.DALLE3_SIZE}')
        self.stdout.write(f'   • DALL-E 3 Quality: {DrawingConfig.DALLE3_QUALITY}')
        
        # Test generation
        if options['generate_test']:
            self.stdout.write('\n' + '='*70)
            self.stdout.write(self.style.WARNING('   Test Drawing Generation (Costs API Credits)'))
            self.stdout.write('='*70 + '\n')
            
            try:
                self.stdout.write('🎨 Generating test P&ID drawing with DALL-E 3...')
                response = client.images.generate(
                    model="dall-e-3",
                    prompt="Simple P&ID diagram showing a pump P-101, valve HV-101, and pressure gauge PI-101 connected by piping, technical engineering style, black on white",
                    size="1792x1024",
                    quality="hd",
                    n=1
                )
                
                image_url = response.data[0].url
                self.stdout.write(self.style.SUCCESS(f'✅ Test drawing generated successfully!'))
                self.stdout.write(f'   Image URL: {image_url[:60]}...')
                self.stdout.write(self.style.SUCCESS('\n✨ P&ID drawing generation is working correctly!\n'))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Test generation failed: {str(e)}'))
                
                if 'billing' in str(e).lower() or 'quota' in str(e).lower():
                    self.stdout.write(self.style.WARNING('\n⚠️  Issue: Billing/Quota Problem'))
                    self.stdout.write('   • Check your OpenAI account billing at: https://platform.openai.com/account/billing')
                    self.stdout.write('   • Ensure you have available credits')
                    self.stdout.write('   • Verify payment method is active\n')
                elif 'rate_limit' in str(e).lower():
                    self.stdout.write(self.style.WARNING('\n⚠️  Issue: Rate Limit Exceeded'))
                    self.stdout.write('   • Wait a moment and try again')
                    self.stdout.write('   • Check rate limits at: https://platform.openai.com/account/limits\n')
                else:
                    self.stdout.write(self.style.WARNING(f'\n⚠️  Unexpected error: {str(e)}\n'))
        else:
            self.stdout.write('\n💡 To test actual drawing generation (costs API credits):')
            self.stdout.write('   python manage.py test_dalle --generate-test\n')
        
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(self.style.SUCCESS('   Test Complete'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))
