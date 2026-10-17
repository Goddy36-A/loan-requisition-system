from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from . import services, validators
from .models import *


class StyledFormMixin:
    """Gives every widget Bootstrap classes and marks invalid fields, so templates can render fields uniformly."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            cls = "form-select" if isinstance(w, forms.Select) else "form-check-input" if isinstance(w, forms.CheckboxInput) else "form-control"
            w.attrs["class"] = (w.attrs.get("class", "") + " " + cls).strip()

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                w = self.fields[name].widget
                w.attrs["class"] = (w.attrs.get("class", "") + " is-invalid").strip()


class StyledAuthForm(StyledFormMixin, AuthenticationForm): pass
class StyledRegisterForm(StyledFormMixin, UserCreationForm): pass
class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm): pass


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta: model = Profile; fields = ["phone", "nin"]

    def clean_phone(self):
        v = self.cleaned_data.get("phone", "")
        return validators.clean_phone(v) if v.strip() else ""

    def clean_nin(self):
        n = validators.clean_nin(self.cleaned_data.get("nin", ""))
        if n and Profile.objects.filter(nin=n).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This NIN is already registered to another account.")
        return n


class AppForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LoanApplication
        fields = ["branch", "amount_requested", "purpose", "repayment_period", "disbursement_account", "employment_status",
                  "employer_name", "monthly_income", "monthly_expenses", "existing_debt", "guarantor_name", "guarantor_phone"]
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        f = self.fields["disbursement_account"]
        f.label, f.required, f.empty_label = "Pay the loan into", False, "My primary account"
        f.queryset = services.active_accounts(user) if user else BankAccount.objects.none()
        f.label_from_instance = lambda a: f"{a.bank_name} - {a.account_name} ({a.masked})"

    def clean_guarantor_phone(self):
        v = self.cleaned_data.get("guarantor_phone", "")
        return validators.clean_phone(v) if v.strip() else ""


class PaymentForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2)
    method = forms.ChoiceField(choices=Payment.METHODS)
    reference = forms.CharField(max_length=60, required=False)
    paid_on = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


class BankAccountForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = BankAccount; fields = ["bank_name", "account_name", "account_number", "branch_name"]
        labels = {"bank_name": "Bank", "account_name": "Name on the account", "account_number": "Account number", "branch_name": "Bank branch (optional)"}
        widgets = {"bank_name": forms.TextInput(attrs={"list": "bank-names", "autocomplete": "off"})}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_account_number(self):
        return validators.clean_account_number(self.cleaned_data["account_number"])

    def clean(self):
        c = super().clean()
        if self.user and not self.errors:
            mine = services.active_accounts(self.user)
            if mine.count() >= services.MAX_BANK_ACCOUNTS:
                raise ValidationError(f"You can keep at most {services.MAX_BANK_ACCOUNTS} bank accounts. Remove one first.")
            if mine.filter(bank_name__iexact=c["bank_name"].strip(), account_number=c["account_number"]).exists():
                raise ValidationError("This bank account is already on your profile.")
        return c


class NextOfKinForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = NextOfKin; fields = ["full_name", "relationship", "phone", "address"]
        labels = {"full_name": "Full name", "address": "Address (optional)"}

    def clean_phone(self):
        return validators.clean_phone(self.cleaned_data["phone"])


class WitnessForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Witness; fields = ["full_name", "phone", "nin", "relationship", "address"]
        labels = {"full_name": "Full name", "nin": "National ID number (optional)", "address": "Address (optional)"}

    def clean_phone(self):
        return validators.clean_phone(self.cleaned_data["phone"])

    def clean_nin(self):
        return validators.clean_nin(self.cleaned_data.get("nin", ""))
