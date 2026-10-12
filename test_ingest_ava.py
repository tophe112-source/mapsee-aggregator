"""Does the AVA adapter read a volksmarch the way the club wrote it?

Every fixture below is my.ava.org's own markup from the live pass of
2026-10-11 (the year list and seven event pages, trimmed to the parts the
adapter reads; long prose keeps its first sentence and any about price). Each
check is a trap that pass measured:

  * a distance read as a clock ("10K will start at 9:00am" opened at 10:00),
    a month's day read as one ("Oct. 17: Start Table 9:00 AM" became 5 am), and
    "closes at" taken for the end of a walk that closes its START table;
  * "free": 35 of the 122 pages state a walker fee, usually $4 (five of them in
    ways the first draft missed: "Parking limited before 9:30 AM Walk fee
    $4.00."), 4 say a fee is required with no amount, 1 says everyone walks
    free, and "Children under 12 walk for free" is not free for everyone; the
    free row says "Free to join.", which ../mapsee migration 0227's free
    predicate reads (offers_0227), and a hotel's "reduced rate" or a parking
    "not free" never reaches the description;
  * the start place typed for a map app ("Get directions to ...", a street with
    its town glued on) and a venue in the town's cell;
  * "New Year's Eve Walk" put on the party door by the sync's nightlife rule;
  * a cancellation written into the title, whose tombstone must carry the
    live row's fingerprint; a club's double listing; a town cell that is a park;
  * only events inside the window cost a request, and the store is saved as
    the run goes.

Pure functions, literal pages and a fake client: no network, no store file.
    MAPSEE_TODAY is not needed: every date here is passed explicitly.
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import mapsee_ingest_ava as A
import offers_0227 as O
from mapsee_ingest import EventStore
from mapsee_supabase_sync import DESCRIPTION_MAX, derive_categories, to_row

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = date(2026, 10, 11)

# --------------------------------------------------------------------------- #
# fixtures: my.ava.org, 2026-10-11 22:03-22:05 UTC
# --------------------------------------------------------------------------- #
LIST = '''<table class='indent bordered'>
<tr style='background: #90F0FF;'>
<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2026&nbsp;-&nbsp;October</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=131578&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=131578>Add</button></td>
<td class='bordered'>16 - 18</td>
<td class='bordered'><img src='graphics/glyphs/bike-glyph.png' alt='Bike' height='20'></td>
<td class='bordered'>&nbsp;</td>
<td class='bordered'>Arlington,<br>VA</td>
<td class='bordered'>131578 -<br>US FreedomWalk Festival Bike</td>
<td class='bordered'>U.S. Freedom Walk Festival Club</td>
</tr>
<tr>
<td class='noborder' colspan='9' >Start/Finish times for event weekend are as follows: Friday  10 am to 3 pm, Saturday 8 am to 2:00 pm, Sunday 8 am to 2 pm. Finish table closes on Friday at 6 pm, Saturday at 5 pm, and Sunday at 4 pm.</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=131979&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=131979>Add</button></td>
<td class='bordered'>17</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>1A</td>
<td class='bordered'>Cottleville,<br>MO</td>
<td class='bordered'>131979 -<br>Small city and paved trails!</td>
<td class='bordered'>Gateway Milers NFP</td>
</tr>
<tr>
<td class='noborder' colspan='9' >Both the 5k and 10k will explore a part of downtown Cottleville and trails in the park.  The 10k also includes strolling through the campus of St Charles Community College.</td>
</tr>
<tr style='background: #90F0FF;'>
<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2026&nbsp;-&nbsp;November</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=129527&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=129527>Add</button></td>
<td class='bordered'>14</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>1A</td>
<td class='bordered'>Dayton,<br>OH</td>
<td class='bordered'>129527 -<br>OWF Honoring Veterans Event</td>
<td class='bordered'>Ohio Wander Freunde</td>
</tr>
<tr>
<td class='noborder' colspan='9' >AVA 50th Anniversary feature event, revisiting the 1st OWF  walk, held on 9/23/1978. Walk the Museum of the United States Air Force, enjoying the history of flight, which started right here in Dayton.</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=132078&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=132078>Add</button></td>
<td class='bordered'>14</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>1A</td>
<td class='bordered'>Annapolis,<br>MD</td>
<td class='bordered'>132078 -<br>America 250 Walk</td>
<td class='bordered'>Annapolis Amblers</td>
</tr>
<tr>
<td class='noborder' colspan='9' >Come celebrate America 250 with a special walk through Historic Annapolis including docents and re-enactors sharing historical information at various sites.</td>
</tr>
<tr style='background: #90F0FF;'>
<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2026&nbsp;-&nbsp;December</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=131756&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=131756>Add</button></td>
<td class='bordered'>12</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>1A</td>
<td class='bordered'>Omaha,<br>NE</td>
<td class='bordered'>131756 -<br>Holiday Lights Walk</td>
<td class='bordered'>Nebraska Wander Freunde Trailblazers</td>
</tr>
<tr>
<td class='noborder' colspan='9' >A walking tour of the spectacular light display in the downtown area and the Heartland of America Park. Enjoy cookies and door prizes after the walk.  Everyone walks for free!</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=133359&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=133359>Add</button></td>
<td class='bordered'>12</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>2A</td>
<td class='bordered'>Maryville,<br>TN</td>
<td class='bordered'>133359 -<br>Blount County Jaycees' Christmas Parade</td>
<td class='bordered'>East Tennessee Wanderers</td>
</tr>
<tr>
<td class='noborder' colspan='9' >Celebrate the Holidays.  Throw candy to the eager children.</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=129896&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=129896>Add</button></td>
<td class='bordered'>31</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>2A</td>
<td class='bordered'>Seattle,<br>WA</td>
<td class='bordered'>129896 -<br>New Year's Eve Walk</td>
<td class='bordered'>Emerald City Wanderers</td>
</tr>
<tr>
<td class='noborder' colspan='9' >We'll see  Christmas lights in W Seattle and then walk down to Elliott Bay, viewing downtown and shipping lights from Alki and Harbor Avenue.</td>
</tr>
<tr style='background: #90F0FF;'>
<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2027&nbsp;-&nbsp;October</td>
</tr>
<tr>
<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=134090&quot;)'>View</button></td>
<td class='bordered'><button class='submit-s2' name='add' value=134090>Add</button></td>
<td class='bordered'>05</td>
<td class='bordered'><img src='graphics/glyphs/walk-glyph.png' alt='Walk' height='20'></td>
<td class='bordered'>1A</td>
<td class='bordered'>Eugene,<br>OR</td>
<td class='bordered'>134090 -<br>River walk</td>
<td class='bordered'>Albany Fitwalkers</td>
</tr>
</table>'''

PAGE_131979 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>Small city and paved trails!</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 131979 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Sat, Oct 17th, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: 9 am-9:30 am off trail by 11:30</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Cottleville, Missouri</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>Legacy Park Cottleville MO</p>
<script>var map_center = new google.maps.LatLng( 38.75122, -90.65127 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p><p class='indent2 text'>None provided</p>				<p class='indent2 note text'>CC: St. Charles County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>Legacy Park includes playgrounds, a dog park, 6 pavilions, athletic fields, trails, and a log cabin home originally owned by the Sargent Family.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 1A, Distance: 5, 10km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: Yes</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Easy</p>
</td>
<td>
<p class='notice text'>Wheelchairs: Medium</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>Legacy Park includes playgrounds, a dog park, 6 pavilions, athletic fields, trails, and a log cabin home.</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>Legacy Park, 5490 Fifth Street, Cottleville, MO  is located on Highway N just west of Cottleville City Hall.  Registration and parking will be by pavilion by the dog park.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=Legacy+Park+Cottleville+MO&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>Legacy Park Cottleville MO</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=Legacy+Park+Cottleville+MO' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>Gateway Milers NFP (AVA-0974)</p><p class='large notice text'>Event website: <a href='http://www.gatewaymilers.org' target='_blank'>www.gatewaymilers.org</a></p></div>'''

PAGE_131756 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>Holiday Lights Walk</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Night Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 131756 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Sat, Dec 12th, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: 4:00 p.m. to 6:00 p.m.; off trail by 8:00 p.m.</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Omaha, Nebraska</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>KANEKO, Omaha, NE</p>
<script>var map_center = new google.maps.LatLng( 41.2532, -95.93162 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p>			<p class='indent2 text'>Anniversaries Hurrah</p>			<p class='indent2 text'>Par-For-The-Course </p>			<p class='indent2 text'>Step to the Beat</p>				<p class='indent2 note text'>CC: Douglas County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>Parking in the Old Market is not free.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 1A, Distance: 5, 10km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: Yes</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Easy</p>
</td>
<td>
<p class='notice text'>Wheelchairs: Medium</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>Downtown Omaha and nearby areas shine brightly during the holidays! And thanks to our sponsors,  everyone walks free!</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>KANEKO, 1111 Jones St, Omaha, Douglas County, NE 68102. From the east: I-480 into NE, left at 10th St. to Jackson St. From west: I-480 northbound to Leavenworth St. Right on Leavenworth to S 13th St. Left on S 13th to Jones. Pay lot at 10th &amp; Jackson; short term metered parking in area with parking garages to northeast.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=KANEKO%2C+Omaha%2C+NE&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>KANEKO, Omaha, NE</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=KANEKO%2C+Omaha%2C+NE' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>Nebraska Wander Freunde Trailblazers (AVA-0016)</p><p class='large notice text'>Event website: <a href='http://www.nebraskatrailblazers.com/' target='_blank'>www.nebraskatrailblazers.com/</a></p></div>'''

PAGE_132078 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>America 250 Walk</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 132078 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Sat, Nov 14th, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: Start open - 10:00am-1:00pm.</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Annapolis, Maryland</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>Annapolis Visitor Center, 26 West St, Annapolis, MD 21401</p>
<script>var map_center = new google.maps.LatLng( 38.9741, -76.50567 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p>			<p class='indent2 text'>Step to the Beat</p>			<p class='indent2 text'>Walking the United States - Capitals</p>			<p class='indent2 text'>Walking with America's Veterans</p>			<p class='indent2 text'>Washington-Rochambeau National Historic Trail</p>				<p class='indent2 note text'>CC: Anne Arundel County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>The fee for Non-IVV &amp; IVV credit is $4.00. Children under 12 walk for free. This walk travels around Downtown Annapolis traveling past a variety of historic sites tied to the American Revolution.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 1A, Distance: 5, 10km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: Yes</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Medium</p>
</td>
<td>
<p class='notice text'>Wheelchairs: Medium</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>We recommend carpooling.  Restrooms are at the start point.  Restrooms are available at various points on the trails.</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>From US 50 East or West, exit at Rowe Blvd. Cross bridge and go straight at light. Turn right before Church Circle to parking garage.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=Annapolis+Visitor+Center%2C+26+West+St%2C+Annapolis%2C+MD+21401&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>Annapolis Visitor Center, 26 West St, Annapolis, MD 21401</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=Annapolis+Visitor+Center%2C+26+West+St%2C+Annapolis%2C+MD+21401' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>Annapolis Amblers (AVA-0595)</p><p class='large notice text'>Event website: <a href='http://www.annapolisamblers.org' target='_blank'>www.annapolisamblers.org</a></p></div>'''

PAGE_133359 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>Blount County Jaycees' Christmas Parade</p>		<p class='large notice text' style='margin-bottom:8px'>Traveling Guided Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 133359 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Sat, Dec 12th, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: 10K will start at 9:00am, 5K starts at 11:00 with parade.  (Line-up at 10:30)</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Maryville, Tennessee</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'></p>
<script>var map_center = new google.maps.LatLng( 35.76401, -83.98067 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p><p class='indent2 text'>None provided</p>				<p class='indent2 note text'>CC: Blount County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>None provided</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 2A, Distance: 5, 10km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: No</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Medium</p>
</td>
<td>
<p class='notice text'>Wheelchairs: Medium</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>Participating in the annual community parade.</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>Park in plaza near Midland Restaurant, 155 N Calderwood St., Alcoa, TN.  The parade lineup will be near the intersection of Joule St. and N. Rankin Rd.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='space-above indent2 text'>The address / location has not been set.</p>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>East Tennessee Wanderers (AVA-0948)</p></div>'''

PAGE_129896 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>New Year's Eve Walk</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Night Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 129896 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Thu, Dec 31st, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: 3:30 - 6:30 PM,  closes 9:30 PM</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Seattle, Washington</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>St John the Baptist Episcopal Church Seattle</p>
<script>var map_center = new google.maps.LatLng( 47.57713, -122.38662 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p><p class='indent2 text'>None provided</p>				<p class='indent2 note text'>CC: King County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>This is a favorite event for many volkswalkers and many area residents.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 2A, Distance: 5(1A), 10km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: Yes</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Hard</p>
</td>
<td>
<p class='notice text'>Wheelchairs: No</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>None provided</p>		<p class='space-above large notice text'>Awards: 'A' Award available</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>St John the Baptist Episcopal Church  3050 California Ave.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=St+John+the+Baptist+Episcopal+Church+Seattle&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>St John the Baptist Episcopal Church Seattle</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=St+John+the+Baptist+Episcopal+Church+Seattle' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>Emerald City Wanderers (AVA-0318)</p><p class='large notice text'>Event website: <a href='http://www.emeraldcitywanderers.org/new-years/' target='_blank'>www.emeraldcitywanderers.org/new-years/</a></p></div>'''

PAGE_129527 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>OWF Honoring Veterans Event</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Walk</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 129527 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Sat, Nov 14th, 2026 for 1 day</p>	<p class='space-above indent text'>Start table open: Register between 9:00a - Noon. Finish by 3:00p.</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Dayton, Ohio</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>Get directions to Comfort Suites Dayton Wright Patterson.</p>
<script>var map_center = new google.maps.LatLng( 39.78481, -84.11822 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p>			<p class='indent2 text'>Anniversaries Hurrah</p>			<p class='indent2 text'>Rails-To-Trails</p>			<p class='indent2 text'>Step to the Beat</p>			<p class='indent2 text'>Walking with America's Veterans</p>				<p class='indent2 note text'>CC: Montgomery County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>There will be a 5K trail, rated 1A and a continuous 10K trail, rated 2A.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 1A, Distance: 5, 10(2A)km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: No</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: Hard</p>
</td>
<td>
<p class='notice text'>Wheelchairs: No</p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>ALL participants must pay $4.00. Free parking near the start/hotel. Pre-registration DEADLINE is October 30th, 2026.There are rooms available at the Comfort Suites Dayton-Wright Patterson, for $109 + tax per night.</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>Comfort Suites Dayton-Wright Patterson, 5220 Huberville Avenue, Dayton, Ohio 45431.</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=Get+directions+to+Comfort+Suites+Dayton+Wright+Patterson.&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>Get directions to Comfort Suites Dayton Wright Patterson.</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=Get+directions+to+Comfort+Suites+Dayton+Wright+Patterson.' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>Ohio Wander Freunde (AVA-0002)</p></div>'''

PAGE_131578 = '''<img class='png-line-space' src='graphics/lines/evnt-what.png' alt='WHAT IS THE EVENT'/>
<div class='indent'><p class='large note text' style='margin-bottom:8px'>US FreedomWalk Festival Bike</p>		<p class='large notice text' style='margin-bottom:8px'>Traditional Bike</p>		<p class='large notice text' style='margin-bottom:8px'>SN: 131578 - <span class='notice'>2026</span></p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-when.png' alt='when is the event'/>
<div class='indent'>	<p class='large alert text space-below'>Future event (not yet open)</p>	<p class='large notice text'>Starting Fri, Oct 16th, 2026 for 3 days</p>	<p class='space-above indent text'>Start table open: Friday 10-3, Saturday 8-2, Sunday 8-2</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-where.png' alt='where is the event'/>
<div class='indent'>	<p class='large note text space'>Arlington, Virginia</p><p class='text space-below'> The address/location to the start table for mapping services such as Google or Siri is as follows:</p><p class='indent2 large notice text'>American Legion Post 139, 3445 Washington Blvd. Arlington, VA 22203</p>
<script>var map_center = new google.maps.LatLng( 38.88656, -77.10247 );</script><p class='space-above indent2 small alert text' style='margin-right:30px'>WARNING: The map pin is for
reference only and may not accurately indicate the start box location or the event start.</p>	</div></div><div class='column-right'>	<img class='png-line-space' src='graphics/lines/evnt-why.png' alt='reasons to do the event'/>
<div class='indent'>
<table class='basic space_below'>
<tr>
<td>					<p id='sp' class='large notice text'>Special programs</p>			<p class='indent2 text'>Anniversaries Hurrah</p>			<p class='indent2 text'>Rails-To-Trails</p>			<p class='indent2 text'>Walking with America's Veterans</p>			<p class='indent2 text'>Washington-Rochambeau National Historic Trail</p>				<p class='indent2 note text'>CC: Arlington County</p>				</td>
<td>
</td>
</tr>
</table>		<p class='large notice text'>Event comments</p>		<p class='indent2 text'>Welcome to the Arlington Loop-The Arlington Loop is a 16-mile circuit composed of four local trails.</p>	</div>	<img class='png-line-space dynamic-pad-top-add-15' src='graphics/lines/evnt-why-not.png' alt='conditions for the event'/>
<div class='indent'>	<p class='large notice text space'>Rating: 1C, Distance: 13, 31km</p>		<table class='indent full-width' style='margin-bottom:10px'>
<tr>
<td width='50%'>
<p class='notice text'>Restrooms: Yes</p>
</td>
<td>
<p class='notice text'>Pets: No</p>
</td>
</tr><tr>
<td>
<p class='notice text'>Strollers: </p>
</td>
<td>
<p class='notice text'>Wheelchairs: </p>
</td>
</tr>
</table>		<p class='large notice text'>Event information</p>		<p class='indent2 text'>None provided</p>		<p class='space-above large notice text'>Awards: IVV CREDIT ONLY</p>	</div>	<img class='png-line-space' src='graphics/lines/evnt-directions.png' alt='how to get to the event'/>
<div class='indent'>	<p class='large notice text'>Driving to the start</p><p class='indent2 text'>LOCATION: All walks begin and end at the American Legion Post 139, 3445 Washington Blvd., Arlington, VA 22201, .3 of a mile from the Virginia Square Metro-rail Station N Monroe St, Arlington, VA 22203.The station which is part of the Washington Metro-rail system (www.wmata.com).</p>	<p class='space-above large notice text'>Maps phrase and directions link</p><p class='indent text'>Click "GO" to bring up the directions on Google maps.</p>
<table class='space-above full-width'><tr>
<td>
<a href='https://www.google.com/maps/dir/?api=1&destination=American+Legion+Post+139%2C+3445+Washington+Blvd.+Arlington%2C+VA+22203&travelmode=driving' target='_blank'><input type='button' class='submit-s1 indent' value='GO'/></a>
</td>
<td>
<p class='indent large text'>American Legion Post 139, 3445 Washington Blvd. Arlington, VA 22203</p>
</td>
</tr><tr>
<td></td>
<td>
<a href='https://maps.google.com/maps/search/?api=1&query=American+Legion+Post+139%2C+3445+Washington+Blvd.+Arlington%2C+VA+22203' target='_blank'><input type='button' class='submit-s1 indent'
value='Just map the above location'/></a>
</td>
</tr></table>	</div><img class='png-line-space' src='graphics/lines/evnt-sponsor.png' alt='event sponsor'/>
<div class='indent'>	<p class='large notice text'>U.S. Freedom Walk Festival Club (AVA-0901)</p><p class='large notice text'>Event website: <a href='http://www.usfreedomwalk.org/' target='_blank'>www.usfreedomwalk.org/</a></p></div>'''


PAGES = {"131979": PAGE_131979, "131756": PAGE_131756, "132078": PAGE_132078, "133359": PAGE_133359,
         "129896": PAGE_129896, "129527": PAGE_129527, "131578": PAGE_131578}

# --------------------------------------------------------------------------- #
# the year list
# --------------------------------------------------------------------------- #
rows = A.parse_list(LIST)
by = {r["sn"]: r for r in rows}
check("the list yields every event row, blurb rows and month headings aside",
      sorted(by) == sorted(list(PAGES) + ["134090"]), sorted(by))
check("a day span within the month: '16 - 18' is Oct 16 to Oct 18",
      (by["131578"]["first"], by["131578"]["last"]) == (date(2026, 10, 16), date(2026, 10, 18)),
      (by["131578"]["first"], by["131578"]["last"]))
check("the type comes from the glyph", by["131578"]["type"] == "Bike" and by["131979"]["type"] == "Walk")
check("the month heading carries the year: '05' under 2027 - October is 2027-10-05",
      by["134090"]["first"] == date(2027, 10, 5), by["134090"]["first"])
check("town and state split off the '<br>' cell", (by["131979"]["city"], by["131979"]["region"]) == ("Cottleville", "MO"))
check("the one-line blurb row attaches to the event above it",
      by["131756"].get("blurb", "").endswith("Everyone walks for free!"), by["131756"].get("blurb"))

# --------------------------------------------------------------------------- #
# the start-table window (rule 2), every string verbatim from the live pass
# --------------------------------------------------------------------------- #
WINDOWS = [
    ("9 am-9:30 am off trail by 11:30", (9, 0), (11, 30)),
    ("8-11 am Finish by 3 pm", (8, 0), (15, 0)),
    ("3:30 - 6:30 PM, closes 9:30 PM", (15, 30), None),          # the start table closes
    ("9am-11am; closes at 11am", (9, 0), None),
    ("10K will start at 9:00am, 5K starts at 11:00 with parade. (Line-up at 10:30)", (9, 0), None),
    ("Saturday 42 km start 7-9 am; 21 km start 8-10 am; 6/12 km start 9:00 am - noon. All Finish by 5 pm",
     (7, 0), (17, 0)),
    ("Friday start time for 5 & 10 km - registration opens at 10 am. start times for walk are 12/noon "
     "to 3 pm. Finish by 6 pm", (10, 0), (18, 0)),
    ("Oct. 17: Start Table 9:00 AM; Last Registration 12:00 PM; Closes 4:00 PM.", (9, 0), None),
    ("Nov 6: 12-5 p.m. Nov 7: 9 a.m.-5 p.m.", (12, 0), None),
    ("Daylight hours, 7 days per week.", None, None),
    ("Start: 9 AM - 12 NoonFinish: 3 PM", (9, 0), (15, 0)),
    ("8:30 am to 11:00 am; off trails by Noon", (8, 30), (12, 0)),
    ("8:00 am - 11:00 am, finish walk by 2:00 pm.", (8, 0), (14, 0)),
    ("Start: 1 p.m. - 5 p.m.; Finish NLT 8 p.m.", (13, 0), (20, 0)),
    ("10:00 to noon", (10, 0), None),
    ("3 - 6 PM", (15, 0), None),
    ("9 - Noon", (9, 0), None),
    ("12:30-1 pm", (12, 30), None),
    ("Register between 9:00a - Noon. Finish by 3:00p.", (9, 0), (15, 0)),
    ("TBD", None, None),
    ("Pool operational hours", None, None),
    # no am/pm anywhere: 1-6 is the afternoon (sn 131781 and 132650, live)
    ("9-11, Finish by 1", (9, 0), (13, 0)),
    ("8:30 - 11:30, finish by 2:30", (8, 30), (14, 30)),
    # "ends" inside a word is not a finish (the review's probes, 2026-10-12)
    ("Weekends 9 am - 3 pm", (9, 0), None),
    ("Saturday 8am-noon; weekends 9-1", (8, 0), None),
    ("9:00 am - 12:00 pm, friends 2 welcome", (9, 0), None),
    # a finish before the start is a misreading, not an end: Omaha's window with
    # its last "p.m." dropped
    ("4:00 p.m. to 6:00 p.m.; off trail by 8:00", (16, 0), None),
]
for text, opens, finish in WINDOWS:
    got = A.parse_window(text)
    check(f"window {text[:58]!r} -> opens {opens}, finish {finish}", got == (opens, finish), got)

# --------------------------------------------------------------------------- #
# price (rule 5): the club's own sentences
# --------------------------------------------------------------------------- #
PRICES = [
    ("The fee for Non-IVV & IVV credit is $4.00. Children under 12 walk for free.", ("$4", False)),
    ("And thanks to our sponsors, everyone walks free! Bring hats.", (None, True)),
    ("Children under 12 walk for free.", (None, False)),
    ("All walkers will be assessed the $4 walker's fee.", ("$4", False)),
    ("Exact change is appreciated ($4). POC Barb Ramsay.", ("$4", False)),
    ("The Best Western is offering special room rates of $84 plus tax per night.", (None, False)),
    ("There is a $2.00 fee for the train station parking lot.", (None, False)),
    ("Registration Fee is $4.00 and (optional) \"A\" Award will be available (Only $5.00 Santa 2026 Logo).",
     ("$4", False)),
    ("Everyone walks for free! The fee for IVV credit is $3.", ("$3", False)),
    # the five the first draft missed (live, 2026-10-11)
    ("Parking limited before 9:30 AM Walk fee $4.00. Children 12 and under walk at no charge with adult.",
     ("$4", False)),
    ("So much history here! $4.00 fee for all walkers unless it's your first AVA walk, or a child not walking "
     "for credit. 3 hour parking limit around the Plaza.", ("$4", False)),
    ("Fees/Awards: All Walkers over 12 years pay the $4.00 participation fee. Children under 12 may earn IVV "
     "credit for no charge.", ("$4", False)),
    ("Award: Bat on Halloween Pumpkin leather pin. Cost: Award, $9; IVVevent book credit, $4; walk only, $4.",
     ("$4", False)),
    ("Registration for all of the pre-cruise and cruise port walks is $4. Other currencies will be considered.",
     ("$4", False)),
    # ...and what the money buys still decides
    ("Walkers can pay $10 for lunch at the finish.", (None, False)),
    # a sentence starts at "$" or a digit too: else "$4 per walker" shares one
    # with "3 hour parking" and the parking word vetoes the fee
    ("A lot of history here! $4 per walker. 3 hour parking limit around the Plaza.", ("$4", False)),
]
for text, want in PRICES:
    got = A.walk_price(text)
    check(f"price {text[:58]!r} -> {want}", got == want, got)

# --------------------------------------------------------------------------- #
# the start place and the town cell
# --------------------------------------------------------------------------- #
PLACES = [
    (("Get directions to Comfort Suites Dayton Wright Patterson.", "Dayton", "OH"),
     ("Comfort Suites Dayton Wright Patterson", None)),
    (("1412 SE 12th Avenue (Tiny's Coffee House), Portland, Oregon", "Portland", "OR"),
     ("Tiny's Coffee House", "1412 SE 12th Avenue")),
    (("United Methodist Church of Savage 9050 Baltimore St. Savage Maryland", "Savage", "MD"),
     ("United Methodist Church of Savage", "9050 Baltimore St.")),
    (("Siri Directions to Marion Township Park in Maria Stein Ohio.", "Maria Stein", "OH"),
     ("Marion Township Park", None)),
    (("Legacy Park Cottleville MO", "Cottleville", "MO"), ("Legacy Park", None)),
    (("South Hill Park, Puyallup, WA", "Puyallup", "WA"), ("South Hill Park", None)),
    (("38.25546", "Warsaw", "MO"), (None, None)),
    (("374 Trevor Lane, Bala Cynwyd, PA 19004", "Bala Cynwyd", "PA"), (None, "374 Trevor Lane")),
]
for (place, city, region), want in PLACES:
    got = A.split_place(place, city, region)
    check(f"start place {place[:50]!r} -> {want}", got == want, got)
TOWNS = [
    (("Clifton Park", "NY", "St. Edward's Church, 569 Clifton Park Center Rd"), ("Clifton Park", None)),
    (("Gemini Springs Park", "FL", "Gemini Springs Park DeBary Florida"), (None, "Gemini Springs Park")),
    (("Holiday Inn Express & Suites", "MO", "Holiday Inn Express and Suites"), (None, "Holiday Inn Express & Suites")),
    (("Preston", "WA", "Preston Park and Ride"), ("Preston", None)),
    (("Bristow, Oklahoma", "OK", "Bristow Train Depot and Museum"), ("Bristow", None)),
    (("St. Louis, MO", "MO", None), ("St. Louis", None)),
    (("HOUSTON", "TX", None), ("Houston", None)),
    (("Puyallup / Grayland Park", "WA", "Grayland Park Puyallup"), ("Puyallup", None)),
    (("Robinson Preserve, Bradenton", "FL", None), ("Bradenton", "Robinson Preserve")),
]
for (cell, region, place), want in TOWNS:
    got = A.clean_city(cell, region, place)
    check(f"town cell {cell!r} -> {want}", got == want, got)

# --------------------------------------------------------------------------- #
# a whole read: the list, then only the in-window pages
# --------------------------------------------------------------------------- #


class FakeClient:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        self.asked.append(url)
        if "regional-te-list" in url:
            return self.pages["list"]
        return self.pages.get(url.rsplit("=", 1)[1])


client = FakeClient(dict(PAGES, list=LIST))
written, cancelled = [], []
rep = A.ingest({"within_days": 120}, client, TODAY, written.append, lambda ev, why: cancelled.append(ev))
check("one list read, then one page per event inside 120 days - none for 2027-10-05",
      len(client.asked) == 8 and not any("134090" in u for u in client.asked), client.asked)
check("all seven in-window events are written", rep["kept"] == 7 and len(written) == 7, rep)
ev = {e.source_id: e for e in written}

e = ev["131979"]
check("Cottleville opens 09:00 and ends at 'off trail by 11:30'",
      (e.start_local, e.end_local) == ("2026-10-17T09:00:00", "2026-10-17T11:30:00"), (e.start_local, e.end_local))
check("the club's pin is the place, kept exact", (e.latitude, e.longitude, e.coords_exact) == (38.75122, -90.65127, True),
      (e.latitude, e.longitude, e.coords_exact))
check("the venue is the start place without its town and state", e.venue_name == "Legacy Park", e.venue_name)
check("a title naming no activity says it is a walk",
      e.name == "Small city and paved trails! (volkssport walk)", e.name)
check("community, with fitness and outdoors", (e.category, e.categories) == ("community", ["fitness", "outdoors"]),
      (e.category, e.categories))
check("the link is the event's own page", e.ticket_url == "https://my.ava.org/event-view.php?sn=131979", e.ticket_url)
check("a page that says nothing about price says nothing about price",
      not e.description.startswith(("Free", "Walker fee")), e.description[:60])

e = ev["133359"]
check("'10K will start at 9:00am' opens at 09:00, not 10:00", e.start_local == "2026-12-12T09:00:00", e.start_local)

e = ev["131756"]
check("Omaha, whose sponsors pay, says 'Free to join.'", e.description.startswith("Free to join."), e.description[:40])
check("...and its 'Parking in the Old Market is not free.' is not copied (0227's FREE_NEG would veto the row)",
      "not free" not in e.description and "pay lot" not in e.description.lower(), e.description)
e = ev["132078"]
check("Annapolis: a $4 fee and children free is not free",
      e.description.startswith("Walker fee $4") and "Free to join" not in e.description, e.description[:60])
e = ev["129527"]
check("Dayton's 'Get directions to ...' is a venue name",
      e.venue_name == "Comfort Suites Dayton Wright Patterson", e.venue_name)
check("Dayton states its $4", e.description.startswith("Walker fee $4"), e.description[:40])

e = ev["131578"]
check("a three-day bike event is one all-day span",
      (e.start_local, e.end_local) == ("2026-10-16", "2026-10-18"), (e.start_local, e.end_local))
check("a title that already names its activity is left alone", e.name == "US FreedomWalk Festival Bike", e.name)

e = ev["129896"]
cat = derive_categories(e.as_record("2026-10-11T00:00:00Z"))
check("'New Year's Eve Walk' at 3:30 pm stays community: a walk is not nightlife",
      cat[0] == "community" and "party" not in (cat[1] or []), cat)
check("the 3:30 window without a marker is the afternoon", e.start_local == "2026-12-31T15:30:00", e.start_local)

for sn, e in sorted(ev.items()):
    check(f"{sn}: the description fits the sync's {DESCRIPTION_MAX} and ends with the source line",
          len(e.description) <= DESCRIPTION_MAX and e.description.endswith("no membership needed."),
          len(e.description))

# a cancelled event is a tombstone, not a row
gone = dict(PAGES, list=LIST)
gone["131979"] = PAGE_131979.replace("Future event (not yet open)", "Event has been CANCELLED")
written2, cancelled2 = [], []
A.ingest({"within_days": 120}, FakeClient(gone), TODAY, written2.append, lambda ev, why: cancelled2.append(ev))
check("a page whose status says cancelled is cancelled, not written",
      [c.source_id for c in cancelled2] == ["131979"] and "131979" not in {w.source_id for w in written2},
      [c.source_id for c in cancelled2])

# A CANCELLATION IN THE TITLE. The tombstone must carry the fingerprint of the
# row as it was written - the title without the word - or the sync finds nothing
# to hide. The first draft built it from "CANCELLED - Small city ..." and missed.
live_fp = ev["131979"].fingerprint
for marked in ("CANCELLED - Small city and paved trails!", "Small city and paved trails! CANCELLED",
               "Small city and paved trails! - cancelled due to weather"):
    page = PAGE_131979.replace(">Small city and paved trails!<", f">{marked}<")
    w3, c3 = [], []
    A.ingest({"within_days": 120}, FakeClient(dict(PAGES, list=LIST, **{"131979": page})), TODAY, w3.append,
             lambda ev, why: c3.append(ev))
    tomb = next((c for c in c3 if c.source_id == "131979"), None)
    check(f"title {marked[:44]!r}: a tombstone with the live row's fingerprint, and no row",
          tomb is not None and tomb.fingerprint == live_fp and "131979" not in {w.source_id for w in w3},
          (tomb and tomb.fingerprint, live_fp))
with tempfile.TemporaryDirectory() as d:
    st = EventStore(os.path.join(d, "s.json"))
    st.upsert(ev["131979"])
    page = PAGE_131979.replace(">Small city and paved trails!<", ">CANCELLED - Small city and paved trails!<")
    A.ingest({"within_days": 120}, FakeClient(dict(PAGES, list=LIST, **{"131979": page})), TODAY, st.upsert,
             lambda e, why: st.cancel(e, why))
    check("...so a store holding the live walk drops it and keeps the tombstone",
          live_fp not in st.records and live_fp in st.tombstones, (len(st.records), list(st.tombstones)))

# A page with no map pin is not written: the pin is the place (rule 1).
no_pin = A.parse_detail(PAGE_131979.replace("google.maps.LatLng( 38.75122, -90.65127 )", "null"))
check("a page with no google.maps.LatLng is not written", A.to_event(by["131979"], no_pin, TODAY, 120) == (None, "no map pin"),
      A.to_event(by["131979"], no_pin, TODAY, 120))
# The page's own date wins, and a page whose date has passed is not written.
moved = A.parse_detail(PAGE_131979.replace("Starting Sat, Oct 17th, 2026", "Starting Sat, Oct 3rd, 2026"))
check("a page whose own date has passed is not written, whatever the list says",
      A.to_event(by["131979"], moved, TODAY, 120) == (None, "past"), A.to_event(by["131979"], moved, TODAY, 120))

# A serial number listed twice costs one request (the copy sits right under
# the original, inside the window).
_row = LIST[LIST.index("<tr>\n<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=131979"):
            LIST.index("<tr style='background: #90F0FF;'>\n<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2026&nbsp;-&nbsp;November")]
twice = LIST.replace(_row, _row + _row)
check("(the list really does carry 131979 twice)", [r["sn"] for r in A.parse_list(twice)].count("131979") == 2)
c2 = FakeClient(dict(PAGES, list=twice))
A.ingest({"within_days": 120}, c2, TODAY, lambda e: None, lambda e, w: None)
check("a serial number the list repeats is read once", sum("sn=131979" in u for u in c2.asked) == 1,
      [u for u in c2.asked if "131979" in u])

# THE ACTIVITY. "Sea Witch Festival Swim" (sn 129766) is typed Bike at 300 m;
# "Pumpkin Paddle" (133194) did not parse in the list and its page names no type.
check("a title naming one activity corrects a listing that is not a walk",
      A._kind_key("Bike", "Traditional Bike", "Sea Witch Festival Swim") == "swim")
check("...but never a walk's: 'Kayak Point Park' typed Walk is a walk",
      A._kind_key("Walk", "Traditional Walk", "Kayak Point Park") == "walk")
check("a title naming two activities leaves the listing's", A._kind_key("Bike", None, "Bike & Walk Day") == "bike")
paddle_row = {"sn": "133194", "first": date(2026, 10, 24), "last": date(2026, 10, 24), "type": "", "rating": "",
              "city": "", "region": "", "name": "", "club": ""}
paddle_page = (PAGE_131979.replace("Small city and paved trails!", "Pumpkin Paddle")
               .replace("<p class='large notice text' style='margin-bottom:8px'>Traditional Walk</p>", "")
               .replace("Starting Sat, Oct 17th, 2026", "Starting Sat, Oct 24th, 2026")
               .replace("Cottleville, Missouri", "Rockport, Texas")
               .replace("Legacy Park Cottleville MO", "1495 E Laurel St, Rockport, TX 78382"))
pe, _w = A.to_event(paddle_row, A.parse_detail(paddle_page), TODAY, 120)
check("'Pumpkin Paddle' with no type is a paddle, not a walk",
      pe is not None and pe.name == "Pumpkin Paddle" and pe.description.startswith("Volkssport paddle by"),
      pe and (pe.name, pe.description[:40]))
check("...and its empty town cell takes the page's WHERE heading: Rockport, TX",
      pe is not None and (pe.city, pe.region, pe.address) == ("Rockport", "TX", "1495 E Laurel St"),
      pe and (pe.city, pe.region, pe.address))
sw_row = dict(by["131578"], sn="129766", name="Sea Witch Festival Swim")
sw = A.parse_detail(PAGE_131578.replace("US FreedomWalk Festival Bike", "Sea Witch Festival Swim")
                    .replace("Rating: 1C, Distance: 13, 31km", "Rating: 1A, Distance: 300m")
                    .replace("Starting Fri, Oct 16th, 2026 for 3 days", "Starting Fri, Oct 16th, 2026 for 1 day"))
se, _w = A.to_event(sw_row, sw, TODAY, 120)
check("the Bike-typed 'Sea Witch Festival Swim' is written as a swim (fitness only), not 'Traditional Bike'",
      se is not None and se.categories == ["fitness"] and se.description.startswith("Volkssport swim by")
      and "Bike" not in se.description.split("\n\n")[0], se and (se.categories, se.description[:50]))

# A CLUB'S DOUBLE LISTING IS ONE ROW, the one with a start place - whichever
# the list gives first (sn 129766 came before 131852 on 2026-10-11).
bare = PAGE_131979.replace("<p class='indent2 large notice text'>Legacy Park Cottleville MO</p>",
                           "<p class='indent2 large notice text'></p>")
row_html = LIST[LIST.index("<tr>\n<td class='bordered'><button class='submit-s2' onclick='window.open(&quot;https://my.ava.org/event-view.php?sn=131979"):
                LIST.index("<tr style='background: #90F0FF;'>\n<td class='bordered' colspan='9' style='font-weight: bold;color: #FF0000;'>2026&nbsp;-&nbsp;November")]
double = LIST.replace(row_html, row_html.replace("131979", "131900") + row_html)
w4 = []
r4 = A.ingest({"within_days": 120}, FakeClient(dict(PAGES, list=double, **{"131900": bare})), TODAY, w4.append,
              lambda e, w: None)
small = [e for e in w4 if e.name.startswith("Small city")]
check("two listings of one outing (club, title, start, pin) are one row, the one with a start place",
      len(small) == 1 and small[0].venue_name == "Legacy Park" and r4["duplicates"] == 1,
      [(e.source_id, e.venue_name) for e in small])
check("...and every other walk is still written", len(w4) == 7, len(w4))

# THE TOWN. Live cells and the club's own addresses, 2026-10-11.
TOWNS_IN = [
    (("1495 E Laurel St, Rockport, TX 78382", "TX", None), "Rockport"),
    (("Register at the Trail Center. 39797 Park Trail, Center City, MN. From North Branch, drive east", "MN", None),
     "Center City"),
    (("Visitor Center10901 Tanager Loop Laurel, MD 20708From BW Parkway towards Baltimore", "MD", None), "Laurel"),
    (("1712 Charles Dr. Papillion, NE Take Cornhusker to S. 66th St.", "NE", None), "Papillion"),
    (("Robinson Preserve North Entrance, Bradenton FL", "FL", None), "Bradenton"),
    (("Gemini Springs Park DeBary Florida", "FL", "Gemini Springs Park"), "DeBary"),
    (("13827 S. River Rd., Grand Rapids, OH 43522Take I-75/US23 N to exit 4B", "OH", None), "Grand Rapids"),
    (("1 Calle San Gerónimo, San Juan 00901; Caribe Hilton Hotel.", "PR", None), "San Juan"),
    (("St. Louis, MO 63102", "MO", None), "St. Louis"),
    (("Holiday Inn Express and Suites", "MO", None), None),
]
for (text, region, venue), want in TOWNS_IN:
    got = A.town_in(text, region, venue)
    check(f"town in {text[:46]!r} -> {want!r}", got == want, got)
LOCALITY = [
    ({"city": "Wild River State Park, MN", "region": "MN"},
     {"place": None, "driving": "Register at the Trail Center. 39797 Park Trail, Center City, MN."},
     ("Center City", "MN", "Wild River State Park")),
    ({"city": "De Soto National Prsv", "region": "FL"},
     {"place": "Robinson Preserve North Entrance, Bradenton FL"}, ("Bradenton", "FL", "De Soto National Prsv")),
    ({"city": "Providence and Grand Rapids", "region": "OH"},
     {"place": "Providence Metropark shelter area", "driving": "13827 S. River Rd., Grand Rapids, OH 43522Take I-75"},
     ("Grand Rapids", "OH", None)),
    ({"city": "San Juan Condado", "region": "PR"},
     {"place": "Caribe Hilton", "driving": "1 Calle San Gerónimo, San Juan 00901; Caribe Hilton Hotel."},
     ("San Juan", "PR", None)),
    ({"city": "Clifton Park", "region": "NY"},
     {"place": "St. Edward's Church, 569 Clifton Park Center Rd", "driving": "Halfmoon, NY 12065"},
     ("Clifton Park", "NY", None)),
    ({"city": "", "region": ""}, {"where": "Rockport, Texas", "place": "1495 E Laurel St, Rockport, TX 78382"},
     ("Rockport", "TX", None)),
]
for row, det, want in LOCALITY:
    got = A.locality(row, det)
    check(f"town cell {row['city']!r} -> {want}", got == want, got)

# A FEE THE SITE STATES WITHOUT AN AMOUNT, in its boxed notice above the event
# (4 pages on 2026-10-11), and a state park's entrance fee (3): neither is free,
# whatever the club's words say.
NOTE = "<p class='space-above indent large in-box-note'>{}</p>\t\t<img class='png-line-space' src='graphics/lines/evnt-what.png'"
fest = A.parse_detail(PAGE_131756.replace(
    "<img class='png-line-space' src='graphics/lines/evnt-what.png'",
    NOTE.format("This event is part of a club festival and an additional fee is required to participate."), 1))
fe, _w = A.to_event(by["131756"], fest, TODAY, 120)
check("'an additional fee is required' says so, and the club's 'everyone walks free' does not make it free",
      fe.description.startswith("A fee is required; see the event page.") and "Free to join" not in fe.description,
      fe.description[:60])
park = A.parse_detail(PAGE_131979.replace(
    "<img class='png-line-space' src='graphics/lines/evnt-what.png'",
    NOTE.format("This event is held inside a state park that has a required entrance fee."), 1))
pk, _w = A.to_event(by["131979"], park, TODAY, 120)
check("a state park's entrance fee is said", "The state park charges an entrance fee." in pk.description,
      pk.description[:80])
check("the page's 'None provided' is no prose (16 of 120 rows carried it)",
      not any("None provided" in e.description for e in ev.values()),
      [sn for sn, e in ev.items() if "None provided" in e.description])

# HOTEL AND PARKING PRICES STAY OUT. Williamsburg's comments (sn 132594), verbatim:
# 0227 tagged the walk "discount" on "reduced rate".
HOTEL = ("The Best Western is offering special room rates of $84 plus tax per night for a double room with 2 queen "
         "beds. Cost with tax is $96.08. To make a reservation at the reduced rate, call (757) 229-4100 and mention "
         "the Peninsula Pathfinders' group. After 1 November 2026, the rate is not guaranteed.")
he, _w = A.to_event(by["131979"], dict(A.parse_detail(PAGE_131979), comments=HOTEL), TODAY, 120)
check("a hotel's room rate is not copied into the walk's description",
      not any(x in he.description for x in ("$84", "reduced rate", "$96.08", "room rate", "not guaranteed")),
      he.description)
check("...and the list's blurb stands in for prose that was all hotel",
      "Both the 5k and 10k will explore" in he.description, he.description[-200:])
check("parking price prose is dropped and the directions around it kept",
      A.side_prices_out("Turn left on Main St. Parking in the Old Market is not free. We suggest using a pay lot. "
                        "There is one at 10th & Jones. Street Meters accept credit cards and cash. The 10k route "
                        "crosses the river.") == "Turn left on Main St. The 10k route crosses the river.")

# --------------------------------------------------------------------------- #
# the HTTP client: refusals, deadline, pace
# --------------------------------------------------------------------------- #


class Resp:
    def __init__(self, code, text="", url="https://my.ava.org/x"):
        self.status_code, self.text, self.url = code, text, url


class Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, 0

    def get(self, url, **kw):
        self.calls += 1
        return self.resp


class Robots:
    def __init__(self, allowed):
        self.allowed = allowed

    def check(self, url):
        return {"allowed": self.allowed, "status": "ok", "rule": "Disallow: /" if not self.allowed else None}


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


check("a 403 is a refusal", raises(lambda: A.Client(Session(Resp(403)), 0, None).get("https://my.ava.org/a"), A.Refused))
check("a 429 is a refusal", raises(lambda: A.Client(Session(Resp(429)), 0, None).get("https://my.ava.org/a"), A.Refused))
check("a bot challenge is a refusal",
      raises(lambda: A.Client(Session(Resp(200, "<title>Just a moment...</title>")), 0, None).get("https://my.ava.org/a"),
             A.Refused))
s = Session(Resp(200, "ok"))
check("robots.txt says no: refused before any request",
      raises(lambda: A.Client(s, 0, None, robots=Robots(False)).get("https://my.ava.org/a"), A.Refused) and s.calls == 0)
check("no request starts after the deadline",
      raises(lambda: A.Client(Session(Resp(200, "ok")), 0, 5.0, clock=lambda: 10.0).get("https://my.ava.org/a"), A.Stop))
slept = []
c = A.Client(Session(Resp(200, "ok")), 1.0, None, clock=lambda: 100.0, sleep=slept.append)
c.get("https://my.ava.org/a")
c.get("https://my.ava.org/b")
check("requests are paced at the config's pause", slept == [1.0], slept)



class DelayRobots:
    def check(self, url):
        return {"allowed": True, "status": "ok", "rule": None, "crawl_delay": 5.0}


slept = []
c = A.Client(Session(Resp(200, "ok")), 1.0, None, robots=DelayRobots(), clock=lambda: 100.0, sleep=slept.append)
c.get("https://my.ava.org/a")
c.get("https://my.ava.org/b")
check("a robots.txt Crawl-delay longer than the pause is adopted", c.pause == 5.0 and slept == [5.0], (c.pause, slept))
check("a redirect off the site is not read",
      A.Client(Session(Resp(200, "ok", url="https://elsewhere.example/x")), 0, None).get("https://my.ava.org/a") is None)


class Down:
    def get(self, url, **kw):
        raise requests.ConnectionError("connection reset")


check("a network error costs the page, not the run",
      A.Client(Down(), 0, None).get("https://my.ava.org/a") is None)

# --------------------------------------------------------------------------- #
# the store is saved as the run goes: a step cancelled by timeout-minutes keeps
# what it read (docs/agents/ci-and-jobs.md). 30 walks on 30 days; when the 28th
# page is asked for, the file on disk must already hold the first 25.
# --------------------------------------------------------------------------- #
DAYS = [date(2026, 10, 12) + timedelta(days=i) for i in range(30)]
MON = {10: "October", 11: "November"}


def list_of(days):
    out, month = ["<table class='indent bordered'>"], None
    for i, d in enumerate(days):
        if d.month != month:
            month = d.month
            out.append(f"<tr style='background: #90F0FF;'>\n<td class='bordered' colspan='9'>{d.year}&nbsp;-&nbsp;"
                       f"{MON[d.month]}</td>\n</tr>")
        out.append(f"<tr>\n<td class='bordered'><button onclick='window.open(&quot;https://my.ava.org/event-view.php?"
                   f"sn={900000 + i}&quot;)'>View</button></td>\n<td class='bordered'>Add</td>\n"
                   f"<td class='bordered'>{d.day}</td>\n<td class='bordered'><img alt='Walk'></td>\n"
                   f"<td class='bordered'>1A</td>\n<td class='bordered'>Cottleville,<br>MO</td>\n"
                   f"<td class='bordered'>{900000 + i} -<br>Walk {i}</td>\n<td class='bordered'>Gateway Milers NFP</td>\n</tr>")
    return "\n".join(out + ["</table>"])


class SiteSession:
    """my.ava.org as the run sees it, which reads the store file at the 28th page."""

    def __init__(self, store_path):
        self.headers, self.pages, self.store_path, self.on_disk = {}, 0, store_path, None

    def get(self, url, **kw):
        if url.endswith("/robots.txt"):
            return SResp(404, "", url)
        if "regional-te-list" in url:
            return SResp(200, list_of(DAYS), url)
        self.pages += 1
        if self.pages == 28:
            self.on_disk = (len(json.load(open(self.store_path, encoding="utf-8"))["events"])
                            if os.path.exists(self.store_path) else 0)
        i = int(url.rsplit("=", 1)[1]) - 900000
        d = DAYS[i]
        return SResp(200, PAGE_131979.replace("Small city and paved trails!", f"Walk {i}")
                     .replace("Starting Sat, Oct 17th, 2026", f"Starting Day, {d.strftime('%b')} {d.day}th, {d.year}"),
                     url)


class SResp:
    def __init__(self, code, text, url):
        self.status_code, self.text, self.url, self.content = code, text, url, text.encode()


with tempfile.TemporaryDirectory() as d:
    cfg_path, store_path = os.path.join(d, "ava.json"), os.path.join(d, "store.json")
    json.dump({"list_url": A.LIST_URL, "detail_url": A.DETAIL_URL, "within_days": 120, "pause": 0}, open(cfg_path, "w"))
    made = []
    saved_session, os.environ["MAPSEE_TODAY"] = requests.Session, "20261011"
    requests.Session = lambda: made.append(SiteSession(store_path)) or made[-1]
    try:
        A.main(["--config", cfg_path, "--store", store_path, "--force"])
    finally:
        requests.Session = saved_session
        os.environ.pop("MAPSEE_TODAY", None)
    final = len(json.load(open(store_path, encoding="utf-8"))["events"])
    check(f"the store is saved every {A.SAVE_EVERY} walks: 25 on disk at the 28th page, 30 at the end",
          made and made[0].on_disk == 25 and final == 30, (made and made[0].on_disk, final))

# --------------------------------------------------------------------------- #
# the free line is a contract with ../mapsee migration 0227's offer tagger,
# judged on the row the sync stores (offers_0227 is that predicate, vendored)
# --------------------------------------------------------------------------- #
drift = O.twin_drift()
if drift is None:
    print("skip the vendored 0227 patterns are not compared: ../mapsee is not checked out here")
else:
    check("offers_0227's patterns equal ../mapsee/tools/measure_deals.py's", drift == [], drift)


def stored_tags(e):
    """0227's tags for the row the sync writes from this event."""
    with tempfile.TemporaryDirectory() as d:
        st = EventStore(os.path.join(d, "s.json"))
        st.upsert(e)
        rec = next(iter(st.records.values()))
    row = to_row(rec, "00000000-0000-0000-0000-000000000000")
    return O.classify({"title": row["title"], "description": row["description"]})[0]


tags = {sn: stored_tags(e) for sn, e in ev.items()}
check("the free walk (Omaha, 'everyone walks free') IS tagged free", "free" in tags["131756"], tags["131756"])
check("a walk with a walker fee is NOT tagged free (Annapolis and Dayton, $4)",
      "free" not in tags["132078"] and "free" not in tags["129527"], (tags["132078"], tags["129527"]))
check("a walk whose page says nothing about price is NOT tagged free (Cottleville)",
      "free" not in tags["131979"], tags["131979"])
check("no walk is tagged a discount, an intro offer, a sale or pay-what-you-can",
      not any(t & {"discount", "intro", "sale", "pwyw", "happyhr"} for t in tags.values()), tags)
check("...not even the one whose club copied its hotel's 'reduced rate' (sn 132594)",
      not (stored_tags(he) & {"discount", "intro"}), stored_tags(he))
check("'A fee is required; see the event page.' claims no freedom and no deal",
      not (O.classify({"title": "Great Columbia Crossing (volkssport walk)",
                       "description": "A fee is required; see the event page."})[0]
           & {"free", "discount", "intro"}))

# --------------------------------------------------------------------------- #
# the config the job runs
# --------------------------------------------------------------------------- #
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ava_sources.json"), encoding="utf-8"))
check("ava_sources.json reads Wednesday and Sunday", cfg.get("run_weekdays") == [2, 6], cfg.get("run_weekdays"))
check("ava_sources.json names the list and the page the adapter reads",
      cfg.get("list_url") == A.LIST_URL and cfg.get("detail_url") == A.DETAIL_URL)
check("one request a second", float(cfg.get("pause", 0)) >= 1.0)

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all AVA checks passed")
