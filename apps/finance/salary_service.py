"""
Salary Calculation Service
Business logic for salary computation
SOFT-CODED for easy customization
"""
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
import logging

from .salary_models import (
    EmployeeSalaryInfo,
    SalaryComponent,
    EmployeeSalaryComponent,
    PayrollRun,
    SalarySlip,
    SalaryStatus,
    SalarySlipAuditLog,
)

logger = logging.getLogger(__name__)


class SalaryCalculationService:
    """
    Service class for salary calculations
    Handles salary slip generation and computation logic
    """
    
    # SOFT-CODED: Tax configuration
    TAX_CONFIG = {
        'tax_rate': Decimal('0.05'),  # 5% tax rate
        'tax_free_threshold': Decimal('5000.00'),  # No tax below this amount
    }
    
    def __init__(self):
        self.logger = logger
    
    @transaction.atomic
    def generate_salary_slip(self, employee, payroll_run, month, year, generated_by):
        """
        Generate salary slip for an employee
        
        Args:
            employee: EmployeeSalaryInfo instance
            payroll_run: PayrollRun instance
            month: int (1-12)
            year: int
            generated_by: User instance
        
        Returns:
            SalarySlip instance
        """
        # Check if slip already exists
        existing_slip = SalarySlip.objects.filter(
            employee_salary_info=employee,
            month=month,
            year=year
        ).first()
        
        if existing_slip:
            raise ValueError(f"Salary slip already exists for {employee.employee_id} for {month}/{year}")
        
        # Get employee's salary components for the period
        period_end = payroll_run.period_end
        
        # Calculate allowances
        allowances = EmployeeSalaryComponent.objects.filter(
            employee_salary_info=employee,
            component__component_type='allowance',
            component__is_active=True,
            is_active=True,
            effective_from__lte=period_end
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=period_end)
        ).select_related('component')
        
        # Calculate deductions
        deductions = EmployeeSalaryComponent.objects.filter(
            employee_salary_info=employee,
            component__component_type='deduction',
            component__is_active=True,
            is_active=True,
            effective_from__lte=period_end
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=period_end)
        ).select_related('component')
        
        # Calculate totals
        basic_salary = employee.basic_salary
        total_allowances = Decimal('0.00')
        total_deductions = Decimal('0.00')
        
        allowances_breakdown = {}
        deductions_breakdown = {}
        
        # Process allowances
        for allowance in allowances:
            amount = self._calculate_component_amount(
                allowance.component,
                allowance.value,
                basic_salary
            )
            total_allowances += amount
            allowances_breakdown[allowance.component.name] = {
                'code': allowance.component.code,
                'amount': str(amount),
                'type': allowance.component.calculation_type
            }
        
        # Process deductions
        for deduction in deductions:
            amount = self._calculate_component_amount(
                deduction.component,
                deduction.value,
                basic_salary
            )
            total_deductions += amount
            deductions_breakdown[deduction.component.name] = {
                'code': deduction.component.code,
                'amount': str(amount),
                'type': deduction.component.calculation_type
            }
        
        # Calculate gross salary
        gross_salary = basic_salary + total_allowances
        
        # Calculate tax
        tax_deduction = self._calculate_tax(gross_salary - employee.tax_exemption)
        total_deductions += tax_deduction
        
        # Calculate net salary
        net_salary = gross_salary - total_deductions
        
        # Generate slip number
        slip_number = self._generate_slip_number(employee, month, year)
        
        # Create salary slip
        salary_slip = SalarySlip.objects.create(
            slip_number=slip_number,
            payroll_run=payroll_run,
            employee_salary_info=employee,
            month=month,
            year=year,
            basic_salary=basic_salary,
            total_allowances=total_allowances,
            gross_salary=gross_salary,
            total_deductions=total_deductions,
            tax_deduction=tax_deduction,
            net_salary=net_salary,
            currency=employee.currency,
            allowances_breakdown=allowances_breakdown,
            deductions_breakdown=deductions_breakdown,
            working_days=30,  # Default, can be customized
            present_days=30,  # Default, can be customized
            absent_days=0,
            status=SalaryStatus.GENERATED,
            generated_by=generated_by
        )
        
        # Log creation
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='created',
            performed_by=generated_by,
            description='Salary slip generated'
        )
        
        self.logger.info(f"Generated salary slip {slip_number} for {employee.employee_id}")
        
        return salary_slip
    
    def _calculate_component_amount(self, component, value, basic_salary):
        """
        Calculate the actual amount for a salary component
        
        Args:
            component: SalaryComponent instance
            value: Decimal value (either fixed amount or percentage)
            basic_salary: Decimal basic salary amount
        
        Returns:
            Decimal: Calculated amount
        """
        if component.calculation_type == 'fixed':
            return value
        elif component.calculation_type == 'percentage':
            return (value / Decimal('100.00')) * basic_salary
        return Decimal('0.00')
    
    def _calculate_tax(self, taxable_income):
        """
        Calculate tax based on taxable income
        SOFT-CODED: Tax calculation logic can be customized
        
        Args:
            taxable_income: Decimal
        
        Returns:
            Decimal: Tax amount
        """
        if taxable_income <= self.TAX_CONFIG['tax_free_threshold']:
            return Decimal('0.00')
        
        tax_amount = taxable_income * self.TAX_CONFIG['tax_rate']
        return tax_amount.quantize(Decimal('0.01'))
    
    def _generate_slip_number(self, employee, month, year):
        """
        Generate unique salary slip number
        Format: SAL-YYYY-MM-EMPID
        
        Args:
            employee: EmployeeSalaryInfo instance
            month: int
            year: int
        
        Returns:
            str: Slip number
        """
        return f"SAL-{year}-{month:02d}-{employee.employee_id}"
    
    def recalculate_slip(self, salary_slip, updated_by):
        """
        Recalculate an existing salary slip
        Useful when employee components change
        
        Args:
            salary_slip: SalarySlip instance
            updated_by: User instance
        
        Returns:
            SalarySlip: Updated instance
        """
        with transaction.atomic():
            # Store old values for audit
            old_values = {
                'gross_salary': str(salary_slip.gross_salary),
                'net_salary': str(salary_slip.net_salary),
            }
            
            # Regenerate calculations
            employee = salary_slip.employee_salary_info
            basic_salary = employee.basic_salary
            
            # Recalculate allowances and deductions
            # (Similar logic to generate_salary_slip)
            # ... implementation here ...
            
            # Update slip
            salary_slip.save()
            
            # Log update
            SalarySlipAuditLog.objects.create(
                salary_slip=salary_slip,
                action='updated',
                performed_by=updated_by,
                old_values=old_values,
                new_values={
                    'gross_salary': str(salary_slip.gross_salary),
                    'net_salary': str(salary_slip.net_salary),
                },
                description='Salary slip recalculated'
            )
            
            return salary_slip


# Import Q for query filtering
from django.db.models import Q
