"""
Daily Analysis Agent Routes - API endpoints for the daily trading analysis agent.
"""
from flask import Blueprint, jsonify, request
from datetime import datetime

from services.utils import clean_nan_values

daily_agent_bp = Blueprint("daily_agent", __name__)


@daily_agent_bp.route('/api/agent/analyze', methods=['POST'])
def agent_run_analysis():
    """Run the daily analysis agent for a specific date (or today)."""
    try:
        from services.daily_analysis_agent import run_daily_analysis
        data = request.get_json(force=True) if request.is_json else {}
        date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))

        report = run_daily_analysis(date_str)
        return jsonify(clean_nan_values(report))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@daily_agent_bp.route('/api/agent/latest')
def agent_latest_report():
    """Get the most recent analysis report."""
    try:
        from services.daily_analysis_agent import get_latest_report
        report = get_latest_report()
        if report:
            return jsonify(clean_nan_values(report))
        return jsonify({'success': False, 'error': 'No analysis reports found. Run an analysis first.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@daily_agent_bp.route('/api/agent/reports')
def agent_list_reports():
    """List all available analysis reports."""
    try:
        from services.daily_analysis_agent import list_reports
        reports = list_reports()
        return jsonify({'success': True, 'reports': reports, 'count': len(reports)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@daily_agent_bp.route('/api/agent/report/<date_str>')
def agent_get_report(date_str):
    """Get a specific date's analysis report."""
    try:
        import os
        import json
        from services.daily_analysis_agent import REPORT_DIR
        report_file = os.path.join(REPORT_DIR, f'analysis_{date_str}.json')
        if not os.path.exists(report_file):
            return jsonify({'success': False, 'error': f'No report for {date_str}'}), 404
        with open(report_file) as f:
            report = json.load(f)
        return jsonify(clean_nan_values(report))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@daily_agent_bp.route('/api/agent/suggestions')
def agent_suggestions():
    """Get just the suggestions from the latest report."""
    try:
        from services.daily_analysis_agent import get_latest_report
        report = get_latest_report()
        if not report:
            return jsonify({'success': False, 'error': 'No reports available'}), 404
        return jsonify({
            'success': True,
            'suggestions': report.get('suggestions', []),
            'analysis_date': report.get('analysis_date'),
            'generated_at': report.get('generated_at'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
