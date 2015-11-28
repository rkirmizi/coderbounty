from .forms import IssueCreateForm, BountyCreateForm, UserProfileForm
from actstream import action
from actstream.models import Action, user_stream
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core import serializers
from django.core.urlresolvers import reverse
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import render_to_response, RequestContext, redirect, render
from django.views.generic import ListView, DetailView
from django.views.generic.edit import UpdateView
from models import Issue, UserProfile, Bounty, Service, Taker
from utils import get_issue_helper, leaderboard, post_to_slack, submit_issue_taker
from wepay import WePay
from time import strftime
import datetime
import json


def parse_url_ajax(request):
    url = request.POST.get('url', '')
    helper = get_issue_helper(request, url)
    issue = helper.get_issue(request, url)
    return HttpResponse(json.dumps(issue))


def home(request, template="index.html"):
    activities = Action.objects.all()[0:10]
    context = {
        'activities': activities,
        'leaderboard': leaderboard(),
    }
    response = render_to_response(template, context, context_instance=RequestContext(request))
    return response


@login_required
def create_issue_and_bounty(request):
    languages = []
    for lang in Issue.LANGUAGES:
        languages.append(lang[0])
    user = request.user

    if request.method == 'GET':
        url = request.GET.get('url')
        if url:
            helper = get_issue_helper(request, url)
            issue_data = helper.get_issue(request, url)
            if not issue_data:
                messages.error(request, 'Please provide an valid issue url')
                return redirect('/post')

            form = IssueCreateForm(
                initial={
                    'issueUrl': request.GET.get('url'),
                    'title': issue_data['title'],
                    'content': issue_data['content'] or "Added from Github"
                })
        else:
            form = IssueCreateForm()
        return render(request, 'post.html', {
            'languages': languages,
            'form': form,
        })
    if request.method == 'POST':
        url = request.POST.get('issueUrl', '')
        if not url:
            messages.error(request, 'Please provide an issue url')
            return render(request, 'post.html', {
                'languages': languages
            })
        helper = get_issue_helper(request, url)
        issue_data = helper.get_issue(request, url)
        if issue_data:
            service = Service.objects.get(name=issue_data['service'])
            instance = Issue(
                number=issue_data['number'],
                project=issue_data['project'],
                user=issue_data['user'],
                service=service
            )
        else:
            return render(request, 'post.html', {
                'languages': languages,
                'message': 'Please provide a propper issue url',
            })
        form = IssueCreateForm(request.POST, instance=instance)
        bounty_form = BountyCreateForm(request.POST)
        bounty_form_is_valid = bounty_form.is_valid()
        if form.is_valid() and bounty_form_is_valid:
            price = bounty_form.cleaned_data['price']
            if int(price) < 5:
                return render(request, 'post.html', {
                    'languages': languages,
                    'message': 'Bounty must be greater than $5',
                })
            try:
                issue = form.save()
            except:
                issue = Issue.objects.get(
                    number=issue_data['number'],
                    project=issue_data['project'],
                    user=issue_data['user'],
                    service=service)

            bounty_instance = Bounty(user=user, issue=issue, price=price)

            data = serializers.serialize('xml', [bounty_instance, ])

            wepay = WePay(settings.WEPAY_IN_PRODUCTION, settings.WEPAY_ACCESS_TOKEN)
            wepay_data = wepay.call('/checkout/create', {
                'account_id': settings.WEPAY_ACCOUNT_ID,
                'amount': request.POST.get('grand_total'),
                'short_description': 'CoderBounty',
                'long_description': data,
                'type': 'service',
                'redirect_uri': request.build_absolute_uri(issue.get_absolute_url()),
                'currency': 'USD'
            })
            if "error_code" in wepay_data:
                messages.error(request, wepay_data['error_description'])
                return render(request, 'post.html', {
                    'languages': languages
                })

            return redirect(wepay_data['checkout_uri'])

        else:
            return render(request, 'post.html', {
                'languages': languages,
                'message': form.errors,
                'errors': form.errors,
                'form': form,
                'bounty_errors': bounty_form.errors,
            })


def list(request):
    issues = Issue.objects.all().order_by('-created')

    context = {
        'issues': issues,
    }
    response = render_to_response('list.html', context, context_instance=RequestContext(request))
    return response


def profile(request):
    """Redirects to profile page if the requested user is loggedin
    """
    try:
        return redirect('/profile/' + request.user.username)
    except Exception:
        return redirect('/')


class UserProfileDetailView(DetailView):
    model = get_user_model()
    slug_field = "username"
    template_name = "profile.html"

    def get_object(self, queryset=None):
        user = super(UserProfileDetailView, self).get_object(queryset)
        UserProfile.objects.get_or_create(user=user)
        return user

    def get_context_data(self, **kwargs):
        context = super(UserProfileDetailView, self).get_context_data(**kwargs)
        context['activities'] = user_stream(self.get_object(), with_user_activity=True)
        return context


class UserProfileEditView(SuccessMessageMixin, UpdateView):
    model = UserProfile
    form_class = UserProfileForm
    template_name = "profiles/edit.html"
    success_message = 'Profile saved'

    def get_object(self, queryset=None):
        return UserProfile.objects.get_or_create(user=self.request.user)[0]

    def get_success_url(self):
        # return reverse("profile", kwargs={'slug': self.request.user})
        return reverse("edit_profile")

    def form_valid(self, form):
        user_id = form.cleaned_data.get("user")
        user = User.objects.get(id=user_id)
        user.first_name = form.cleaned_data.get("first_name")
        user.last_name = form.cleaned_data.get("last_name")
        user.email = form.cleaned_data.get("email")
        user.save()

        return super(UserProfileEditView, self).form_valid(form)

    def get_success_message(self, cleaned_data):
        return self.success_message


class LeaderboardView(ListView):
    template_name = "leaderboard.html"

    def get_queryset(self):
        return User.objects.all().annotate(
            null_position=Count('userprofile__balance')).order_by(
            '-null_position', '-userprofile__balance', '-last_login')

    def get_context_data(self, **kwargs):
        context = super(LeaderboardView, self).get_context_data(**kwargs)
        context['leaderboard'] = leaderboard()
        return context


class IssueDetailView(DetailView):
    model = Issue
    slug_field = "id"
    template_name = "issue.html"

    def get(self, request, *args, **kwargs):
        if self.request.GET.get('checkout_id'):
            wepay = WePay(settings.WEPAY_IN_PRODUCTION, settings.WEPAY_ACCESS_TOKEN)
            wepay_data = wepay.call('/checkout/', {
                'checkout_id': self.request.GET.get('checkout_id'),
            })

            for obj in serializers.deserialize("xml", wepay_data['long_description'], ignorenonexistent=True):
                obj.object.created = datetime.datetime.now()
                obj.object.checkout_id = self.request.GET.get('checkout_id')
                obj.save()
                action.send(self.request.user, verb='placed a $' + str(obj.object.price) + ' bounty on ', target=obj.object.issue)
                post_to_slack(obj.object)

        return super(IssueDetailView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.get_object().status == 'open':
            if self.get_object().get_api_data()['state'] == 'closed':
                print "closed"
                object = self.get_object()
                object.status = 'in review'
                object.save()
        return super(IssueDetailView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(IssueDetailView, self).get_context_data(**kwargs)
        context['leaderboard'] = leaderboard()
        return context

    def get_object(self):
            object = super(IssueDetailView, self).get_object()
            object.views = object.views + 1
            object.save()
            return object


def issueTaken(request):
    if request.method == 'POST':
        issueId = request.POST.get('id')
        _date = strftime("%c")
        today = datetime.datetime.today()
        response_data = {}
        response_data['status'] = 'taken'
        response_data['issueTakenTime'] = _date

        # issue = Issue.objects.get(pk=issueId)
        issue_take_data = {
            "issue": issueId,
            "issueStartTime": today,
            "user": request.user,
            "status": "taken"
        }
        username = issue_take_data["user"]
        response_data['username'] = str(username)
        issueTaken = submit_issue_taker(issue_take_data)
        return HttpResponse(json.dumps(response_data), content_type="application/json")
def issueTakenById(request,id):
    print id
    issue = Issue.objects.get(pk=id)
    taker = Taker.objects.get(issue=issue)
    print taker.issu_id
    response_data = {}
    response_data['status'] = str(taker.status)
    response_data['issueTakenTime'] = str(taker.issueTaken)
    response_data['username'] = str(taker.user)
    return HttpResponse(json.dumps(response_data), content_type="application/json")
