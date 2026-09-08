/* Invite emails.
 *
 * An invited rep should not have to be told a code over the phone: the link in the
 * mail signs them in and the tutorial starts. Sending is best-effort -- if the mail
 * fails the invite is still valid, so the caller gets the link back and can pass it
 * on by hand rather than the whole invite failing on a mail outage.
 */
const KEY = process.env.RESEND_API_KEY || '';
const FROM = process.env.INVITE_FROM || 'Retail Intel <routes@cannacrypted.com>';
const APP_URL = process.env.APP_URL || 'https://zelidav.github.io/jbd-sales-map/';

export const canSend = () => !!KEY;

export async function sendInvite({ to, name, orgName, invitedBy, token, isAdmin }) {
  const link = `${APP_URL}#invite=${token}`;
  const html = `<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#14241a;max-width:560px">
<p>${escapeHtml(name || 'Hi')} —</p>
<p>${escapeHtml(invitedBy || 'Your team')} has added you to <b>${escapeHtml(orgName || 'the field map')}</b>
— every licensed dispensary in New York, ranked by what it actually sells, with day routing built in.</p>
<p style="margin:24px 0"><a href="${link}" style="background:#1f7a44;color:#fff;text-decoration:none;padding:13px 22px;border-radius:10px;font-weight:700;display:inline-block">Open the map &rarr;</a></p>
<p style="font-size:13px;color:#555">The link signs you in and walks you through it — about two minutes. It works once, on any device;
after that the map remembers you.${isAdmin ? ' You are an admin, so you can add people and upload your company’s sales data.' : ''}</p>
<p style="font-size:12px;color:#888">If the button does not work, paste this into your browser:<br>${escapeHtml(link)}</p>
</div>`;

  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: FROM, to: [to], subject: `You have been added to ${orgName || 'the field map'}`, html,
    }),
  });
  if (!res.ok) throw new Error(`Resend ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
