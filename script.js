
        // --- Core Setup & State ---
        const debugLog = (msg, type = 'info') => {
            const container = document.getElementById('debug-log-content');
            if (!container) {
                console.error("Debug container not found for:", msg);
                return;
            }
            const time = new Date().toLocaleTimeString();
            const div = document.createElement('div');
            div.className = type === 'error' ? 'text-red-400' : (type === 'warn' ? 'text-amber-400' : 'text-green-400');
            div.innerHTML = `<span class="opacity-40">[${time}]</span> ${msg}`;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        };

        window.addEventListener('error', (e) => {
            debugLog(`Runtime Error: ${e.message} at ${e.filename}:${e.lineno}`, 'error');
        });
        window.addEventListener('unhandledrejection', (e) => {
            const reason = e.reason && e.reason.message ? e.reason.message : e.reason;
            debugLog(`Promise Rejected: ${reason}`, 'error');
        });

        debugLog('Script module loaded. Starting boot...');

        const updateStatus = (msg) => {
            const el = document.getElementById('last-updated-display');
            if (el) el.innerText = msg;
            debugLog(`Status: ${msg}`, 'info');
            console.log("Status:", msg);
        };

        const reportError = (tag, err) => {
            debugLog(`${tag}: ${err.message || err}`, 'error');
            console.error(tag, err);
            const el = document.getElementById('events-list');
            if (el) el.innerHTML = `<div class="p-8 text-center text-red-500 font-bold uppercase text-[10px] tracking-widest">${tag}: ${err.message || err}</div>`;
        };

        const isPlaceHolder = (val) => {
            if (!val) return true;
            const str = val.toString();
            return str === 'undefined' || str.includes('import.meta') || str.includes('__INJECT_') || str.includes('PLACEHOLDER');
        };

        // Globals
        const localNow = new Date();
        const TODAY_STR = localNow.getFullYear() + '-' + String(localNow.getMonth() + 1).padStart(2, '0') + '-' + String(localNow.getDate()).padStart(2, '0');
        const TODAY = new Date(TODAY_STR);
        const ALL_CATEGORIES = ['HOLIDAY', 'ACADEMIC', 'SPORTS', 'WELLBEING', 'COMMUNITY', 'ARTS', 'TRIP', 'ADMIN', 'OTHER'];
        const ALL_CLASSES = ['N', 'R', 'Rb', 'Y1', 'Y1b', 'Y2', 'Y2b', 'Y3', 'Y3b', 'Y4', 'Y4b', 'Y5', 'Y5b', 'Y6', 'Y6b'];
        const CATEGORY_STYLES = {
            'HOLIDAY': { dot: 'bg-amber-400', label: 'bg-amber-50 text-amber-700 border-amber-100', checkbox: 'bg-amber-500 border-amber-500' },
            'SPORTS': { dot: 'bg-green-400', label: 'bg-green-50 text-green-700 border-green-100', checkbox: 'bg-green-500 border-green-500' },
            'ACADEMIC': { dot: 'bg-blue-400', label: 'bg-blue-50 text-blue-700 border-blue-100', checkbox: 'bg-blue-500 border-blue-500' },
            'WELLBEING': { dot: 'bg-cyan-400', label: 'bg-cyan-50 text-cyan-700 border-cyan-100', checkbox: 'bg-cyan-500 border-cyan-500' },
            'COMMUNITY': { dot: 'bg-purple-400', label: 'bg-purple-50 text-purple-700 border-purple-100', checkbox: 'bg-purple-500 border-purple-500' },
            'ARTS': { dot: 'bg-pink-400', label: 'bg-pink-50 text-pink-700 border-pink-100', checkbox: 'bg-pink-500 border-pink-500' },
            'TRIP': { dot: 'bg-orange-400', label: 'bg-orange-50 text-orange-700 border-orange-100', checkbox: 'bg-orange-500 border-orange-500' },
            'ADMIN': { dot: 'bg-slate-400', label: 'bg-slate-50 text-slate-700 border-slate-100', checkbox: 'bg-slate-500 border-slate-500' },
            'OTHER': { dot: 'bg-gray-300', label: 'bg-gray-50 text-gray-700 border-gray-100', checkbox: 'bg-gray-400 border-gray-400' }
        };

        window.APP_BASE_URL = window.location.origin + window.location.pathname.replace(/\/$/, '').replace(/\/index\.html$/, '');
        if (window.APP_BASE_URL === 'null' || !window.APP_BASE_URL) window.APP_BASE_URL = window.location.origin;

        // --- Main Boot Sequence ---
        async function boot() {
            try {
                updateStatus('Starting...');
                
                // Env Variables
                let SUPABASE_URL = "";
                let SUPABASE_KEY = "";
                try {
                    const env = import.meta.env;
                    if (env) {
                        SUPABASE_URL = env.VITE_SUPABASE_URL || "";
                        SUPABASE_KEY = env.VITE_SUPABASE_ANON_KEY || env.VITE_SUPABASE_KEY || "";
                        debugLog(`Env: URL=${SUPABASE_URL ? 'OK' : 'MISSING'}, KEY=${SUPABASE_KEY ? 'OK' : 'MISSING'}`);
                        const apiBase = env.VITE_API_BASE_URL;
                        if (apiBase && !isPlaceHolder(apiBase)) window.APP_BASE_URL = apiBase.replace(/\/$/, '');
                    }
                } catch(e) { console.warn("Env check skipped", e); }

                // LocalStorage
                updateStatus('Loading State...');
                try {
                    window.showPastEvents = localStorage.getItem('showPastEvents') === 'true';
                    window.selectedClasses = JSON.parse(localStorage.getItem('selectedClasses') || '[]');
                    window.hiddenCategories = JSON.parse(localStorage.getItem('hiddenCategories') || '[]');
                } catch (e) {
                    window.showPastEvents = false;
                    window.selectedClasses = [];
                    window.hiddenCategories = [];
                }
                window.searchQuery = "";
                window.visibleLimit = 10;
                window.allEvents = [];

                // Supabase Init
                updateStatus('Connecting...');
                if (isPlaceHolder(SUPABASE_URL) || isPlaceHolder(SUPABASE_KEY)) {
                    updateStatus('Config Missing');
                    const list = document.getElementById('events-list');
                    if (list) list.innerHTML = `<div class="py-12 px-6 text-center"><div class="mb-4 text-amber-500 font-black uppercase text-[10px] tracking-widest">Configuration Required</div><p class="text-[9px] text-gray-400 leading-relaxed max-w-sm mx-auto uppercase italic">Secret configuration missing.</p></div>`;
                    return;
                }

                if (!window.supabase) {
                    updateStatus('Retrying Library...');
                    debugLog('Supabase library not yet loaded, waiting...', 'warn');
                    await new Promise(r => setTimeout(r, 1000));
                }

                if (window.supabase) {
                    try {
                        window.supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
                        debugLog('Supabase client initialized');
                    } catch (e) {
                        reportError('Supabase Init', e);
                        return;
                    }
                } else {
                    updateStatus('Dependency Error');
                    return;
                }

                // Initialize UI
                updateStatus('Ready');
                setupEventListeners();
                renderClassFilterGrid();
                renderCategoryFilters();

                // Google Analytics
                try {
                    const gaId = import.meta.env.VITE_GA_MEASUREMENT_ID;
                    if (gaId && !isPlaceHolder(gaId)) {
                        const script = document.createElement('script');
                        script.async = true;
                        script.src = `https://www.googletagmanager.com/gtag/js?id=${gaId}`;
                        document.head.appendChild(script);
                        window.dataLayer = window.dataLayer || [];
                        window.gtag = function(){window.dataLayer.push(arguments);}
                        window.gtag('js', new Date());
                        window.gtag('config', gaId);
                    }
                } catch (e) {}

                // Initial Fetch
                await fetchEvents();
            } catch (bootErr) {
                reportError('Boot Failure', bootErr);
            }
        }

        // --- Logic Handlers ---
        
        function saveState() {
            try {
                localStorage.setItem('selectedClasses', JSON.stringify(window.selectedClasses));
                localStorage.setItem('hiddenCategories', JSON.stringify(window.hiddenCategories));
                localStorage.setItem('showPastEvents', window.showPastEvents);
            } catch (e) { console.error("Save state failed", e); }
        }

        window.toggleSelectAllClasses = function() {
            if (window.selectedClasses.length === ALL_CLASSES.length) {
                window.selectedClasses = [];
            } else {
                window.selectedClasses = [...ALL_CLASSES];
            }
            saveState();
            renderClassFilterGrid();
            renderEvents();
        }

        window.toggleClass = function(cls) {
            const index = window.selectedClasses.indexOf(cls);
            if (index > -1) {
                window.selectedClasses.splice(index, 1);
            } else {
                window.selectedClasses.push(cls);
            }
            saveState();
            renderClassFilterGrid();
            renderEvents();
        }

        window.toggleCategory = function(cat) {
            if (window.hiddenCategories.includes(cat)) {
                window.hiddenCategories = window.hiddenCategories.filter(c => c !== cat);
            } else {
                window.hiddenCategories.push(cat);
            }
            saveState();
            renderCategoryFilters();
            renderEvents();
        }

        // --- Render Functions ---

        function renderCategoryFilters() {
            const container = document.getElementById('category-filter-list');
            if (!container) return;
            
            // Sync toggle state
            const pastToggle = document.getElementById('show-past-toggle');
            if (pastToggle) pastToggle.checked = window.showPastEvents;

            container.innerHTML = ALL_CATEGORIES.map(cat => {
                const isHidden = window.hiddenCategories.includes(cat);
                const style = CATEGORY_STYLES[cat] || CATEGORY_STYLES['OTHER'];
                return `
                    <label class="flex items-center p-2 rounded-xl border ${isHidden ? 'bg-gray-50 border-gray-100' : 'bg-white border-blue-50'} cursor-pointer hover:bg-blue-50/50 transition-all">
                        <input type="checkbox" class="sr-only" ${isHidden ? '' : 'checked'} onchange="toggleCategory('${cat}')">
                        <div class="w-4 h-4 rounded border flex items-center justify-center mr-2 ${isHidden ? 'bg-white border-gray-300' : `${style.checkbox} border-transparent`}">
                            ${isHidden ? '' : '<svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>'}
                        </div>
                        <span class="text-[10px] font-black uppercase ${isHidden ? 'text-gray-400' : style.label.split(' ')[1]}">${cat}</span>
                    </label>
                `;
            }).join('');
        }

        window.toggleClassesCollapse = function() {
            const container = document.getElementById('class-grid-container');
            const icon = document.getElementById('class-expand-icon');
            const btn = document.getElementById('class-expand-btn').querySelector('span');
            const isHidden = container.classList.toggle('hidden');
            
            icon.style.transform = isHidden ? 'rotate(0deg)' : 'rotate(180deg)';
            btn.innerText = isHidden ? 'Configure' : 'Close';
        }

        function renderClassFilterGrid() {
            const grid = document.getElementById('class-filter-grid');
            const btn = document.getElementById('toggle-all-classes-btn');
            const summary = document.getElementById('class-selection-summary');
            
            if (btn) btn.innerText = window.selectedClasses.length === ALL_CLASSES.length ? "Clear All" : "Show All";
            if (summary) {
                if (window.selectedClasses.length === 0 || window.selectedClasses.length === ALL_CLASSES.length) {
                    summary.innerText = "All Classes";
                } else {
                    summary.innerText = window.selectedClasses.sort().join(", ");
                }
            }

            grid.innerHTML = ALL_CLASSES.map(cls => {
                const isActive = window.selectedClasses.includes(cls);
                return `
                    <button onclick="toggleClass('${cls}')" class="px-2 py-2 text-[10px] font-black rounded-xl border transition-all ${isActive ? 'bg-blue-600 border-blue-600 text-white shadow-md scale-105' : 'bg-white border-gray-100 text-gray-400 hover:border-blue-200'}">${cls}</button>
                `;
            }).join('');

            // Update Calendar Sync Options
            renderSyncDropdown();
        }

        function renderSyncDropdown() {
            const container = document.getElementById('sync-options-container');
            if (!container) return;

            const selected = window.selectedClasses.sort();
            const options = [];
            let noteHtml = '';

            if (selected.length === 0) {
                noteHtml = `
                    <div class="px-3 py-2 bg-amber-50 border border-amber-100 rounded-xl mb-2">
                        <p class="text-[8px] font-black text-amber-600 uppercase tracking-widest leading-normal">
                            Note: Select classes above to get dedicated sync feeds for specific years.
                        </p>
                    </div>
                `;
                options.push({ label: "All Classes", slug: "All" });
            } else {
                // List each selected class
                selected.forEach(cls => {
                    options.push({ label: `Class ${cls}`, slug: cls });
                });
            }

            container.innerHTML = noteHtml + options.map((opt, idx) => {
                const syncUrl = `${window.APP_BASE_URL}/api/calendar/${opt.slug}.ics`;
                const encodedUrl = encodeURIComponent(syncUrl);
                const googleUrl = `https://www.google.com/calendar/render?cid=${encodedUrl}`;
                const appleUrl = syncUrl.replace('http://', 'webcal://').replace('https://', 'webcal://');

                return `
                    <div class="p-3 bg-slate-50 border border-slate-100 rounded-2xl">
                        <div class="flex items-center justify-between mb-2">
                            <span class="text-[9px] font-black text-slate-800 uppercase tracking-widest">${opt.label}</span>
                            <span class="text-[7px] font-bold text-slate-400 uppercase tracking-tight">Sync Feed</span>
                        </div>
                        <div class="flex gap-2">
                            <a href="${googleUrl}" target="_blank" class="flex-1 flex items-center justify-center gap-1.5 py-1.5 bg-white border border-slate-200 rounded-lg text-[8px] font-black text-slate-600 uppercase tracking-widest hover:bg-slate-50 transition-all">
                                <img src="https://www.gstatic.com/calendar/images/dynamiclogo_2020q4/calendar_31_2x.png" class="w-3 h-3" referrerPolicy="no-referrer">
                                Google
                            </a>
                            <a href="${appleUrl}" class="flex-1 flex items-center justify-center gap-1.5 py-1.5 bg-white border border-slate-200 rounded-lg text-[8px] font-black text-slate-600 uppercase tracking-widest hover:bg-slate-50 transition-all">
                                <svg class="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 384 512"><path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.9 48.6-.7 90.4-82.5 102.6-119.3-65.2-30.7-61.7-90-61.7-91.9zm-56.6-164.2c27.3-32.4 24.8-61.9 24-72.5-24.1 1.4-52 16.4-67.9 34.9-17.5 19.8-27.8 44.3-25.6 71.9 26.1 2 49.9-11.4 69.5-34.3z"></path></svg>
                                Apple
                            </a>
                        </div>
                        <div class="mt-2 flex items-center gap-2">
                            <input type="text" readonly id="url-${idx}" class="flex-1 bg-white border border-slate-100 rounded-md px-1.5 py-1 text-[7px] font-mono text-slate-400 outline-none truncate" value="${syncUrl}">
                            <button onclick="copyToClipboard('url-${idx}', this)" class="p-1 text-slate-300 hover:text-blue-600 transition-colors">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2"></path></svg>
                            </button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        window.copyToClipboard = function(id, btn) {
            const input = document.getElementById(id);
            if (!input) return;
            input.select();
            document.execCommand('copy');
            const originalHTML = btn.innerHTML;
            btn.innerHTML = '<svg class="w-3 h-3 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>';
            setTimeout(() => { btn.innerHTML = originalHTML; }, 2000);
        }

        window.toggleSyncDropdown = function(e) {
            e.stopPropagation();
            const menu = document.getElementById('sync-dropdown-menu');
            const isHidden = menu.classList.toggle('hidden');
            if (!isHidden) {
                // If opening, ensure category panel is hidden
                const panel = document.getElementById('category-panel');
                if (panel) panel.classList.add('hidden');
            }
        }

        async function fetchEvents() {
            const display = document.getElementById('last-updated-display');
            if (display) display.innerText = 'Syncing...';

            if (!window.supabaseClient) return;

            try {
                const { data, error } = await window.supabaseClient
                    .from('events')
                    .select('*')
                    .eq('status', 'approved')
                    .order('event_date', { ascending: true });
                
                if (error) throw error;
                const fetchedCount = (data && data.length) ? data.length : 0;
                debugLog(`Successfully fetched ${fetchedCount} events from database`);
                
                window.allEvents = (data || []).filter(e => e.type !== 'SYSTEM_META').map(e => {
                    let classesArray = e.classes;
                    if (typeof e.classes === 'string') { try { classesArray = JSON.parse(e.classes); } catch (err) { classesArray = [e.classes]; } }
                    return { ...e, classes: Array.isArray(classesArray) ? classesArray : [] };
                });

                const meta = (data || []).find(e => e.type === 'SYSTEM_META');
                if (meta && display) display.innerText = `Synced: ${meta.summary}`;
                else if (display) display.innerText = 'Synced';
                
                renderEvents();
            } catch (err) {
                console.error('Fetch error:', err);
                if (display) display.innerText = 'Sync Failed';
                renderEvents();
            }
        }

        function renderProactiveWidgets(events) {
            const actionContainer = document.getElementById('action-required');
            const actionList = document.getElementById('action-required-list');

            // --- Deadlines Logic ---
            const actionKeywords = ['form', 'payment', 'deadline', 'due', 'permission', 'consent', 'bring', 'return', 'costum', 'homework', 'rsvp', 'meeting', 'consultation', 'appointment', 'zoom'];
            const deadlines = events.filter(e => {
                const d = new Date(e.event_date);
                if (d < TODAY) return false;
                const text = (e.title + ' ' + (e.summary || '')).toLowerCase();
                const aiDeadline = e.is_deadline === true;
                const hasPriorityKeyword = actionKeywords.some(kw => text.includes(kw));
                return aiDeadline || hasPriorityKeyword;
            }).sort((a, b) => new Date(a.event_date) - new Date(b.event_date));

            if (deadlines.length > 0) {
                actionContainer.classList.remove('hidden');
                let visible = deadlines.filter(e => (new Date(e.event_date) - TODAY) <= (10 * 24 * 60 * 60 * 1000));
                
                // User requested: limit to max 5 items
                visible = visible.slice(0, 5);
                
                // Fallback ensure at least 3 if available even if > 10 days away
                if (visible.length < 3 && deadlines.length > 0) visible = deadlines.slice(0, 3);
                
                // Final hard cap at 5
                visible = visible.slice(0, 5);
                
                actionList.innerHTML = visible.map(e => {
                    const diffDays = Math.round((new Date(e.event_date) - TODAY) / (1000 * 60 * 60 * 24));
                    const isUrgent = diffDays <= 2;
                    let relTime = "";
                    if (diffDays === 0) relTime = "TODAY";
                    else if (diffDays === 1) relTime = "TOMORROW";
                    else if (diffDays > 1) relTime = `IN ${diffDays} DAYS`;

                    // Filter classes to show only those that match active filters (or show 'All' if applicable)
                    const relevantClasses = e.classes.filter(c => 
                        window.selectedClasses.length === 0 || window.selectedClasses.includes(c) || c === 'All'
                    );
                    let badges = relevantClasses.slice(0, 2).map(c => 
                        `<span class="px-1.5 py-0.5 bg-white/60 border border-black/5 text-[7px] font-black text-gray-500 rounded-sm uppercase tracking-tighter">${c}</span>`
                    ).join('');
                    
                    if (relevantClasses.length > 2) {
                        badges += `<span class="text-[10px] text-gray-400 font-black ml-0.5 leading-none">...</span>`;
                    }

                    return `
                        <div onclick="document.getElementById('event-${e.id}').scrollIntoView({behavior:'smooth', block:'start'})" 
                             class="py-1.5 px-3 border-l-2 rounded-r-md flex items-center justify-between gap-3 cursor-pointer hover:bg-white transition-all ${isUrgent ? 'bg-amber-50/50 border-amber-400 shadow-sm' : 'bg-slate-50/50 border-slate-300'}">
                            <div class="flex items-center gap-3 flex-1 min-w-0">
                                <div class="flex flex-col w-16 shrink-0">
                                    <span class="text-[9px] font-black ${isUrgent ? 'text-amber-600' : 'text-blue-600'} leading-none whitespace-nowrap">${relTime}</span>
                                    <span class="text-[7px] font-bold uppercase opacity-40 whitespace-nowrap mt-0.5 leading-none">${e.formatted_date_display || e.event_date}</span>
                                </div>
                                <div class="flex items-center gap-2 flex-1 min-w-0">
                                    <span class="text-[10px] font-black ${isUrgent ? 'text-amber-900' : 'text-slate-700'} uppercase truncate">${e.deadline_desc || e.title}</span>
                                    <div class="flex gap-1 shrink-0">
                                        ${badges}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                }).join('');
            } else actionContainer.classList.add('hidden');
        }

        function renderWeekGlance(events) {
            const container = document.getElementById('week-glance-content');
            if (!container) return;
            const start = new Date(TODAY);
            let html = "";

            for (let i = 0; i < 60; i++) {
                const d = new Date(start); d.setDate(start.getDate() + i);
                const dStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                const dayEvs = events.filter(e => e.event_date === dStr);
                const isToday = dStr === TODAY_STR;
                const isPast = dStr < TODAY_STR;
                const isWeekend = d.getDay() === 0 || d.getDay() === 6;
                const dayType = dayEvs.find(e => e.type === 'HOLIDAY') ? 'HOLIDAY' : (isWeekend ? 'WEEKEND' : 'NORMAL');
                
                let dayClass = "bg-white border-gray-100 text-gray-900";
                if (isToday) dayClass = "bg-blue-600 border-blue-600 text-white shadow-lg ring-2 ring-blue-100";
                else if (isPast || dayType === 'HOLIDAY' || dayType === 'WEEKEND') dayClass = "bg-gray-50 border-gray-200 text-gray-400";

                const dots = dayEvs.slice(0, 4).map(e => {
                    const s = CATEGORY_STYLES[e.type] || CATEGORY_STYLES['OTHER'];
                    return `<div class="w-1.5 h-1.5 rounded-full ${s.dot}"></div>`;
                }).join('');
                
                html += `
                <div onclick="scrollToDay('${dStr}')" class="w-14 h-20 shrink-0 rounded-xl flex flex-col items-center justify-center border cursor-pointer transition-all hover:scale-105 active:scale-95 ${dayClass} text-[10px] font-black uppercase overflow-hidden">
                    <span class="opacity-60 text-[8px] leading-tight">${d.toLocaleDateString('en-GB', {weekday:'short'})}</span>
                    <span class="text-lg leading-tight my-0.5">${d.getDate()}</span>
                    <div class="flex flex-wrap gap-0.5 mt-1 px-1 justify-center">
                        ${dots}
                    </div>
                </div>`;
            }
            container.innerHTML = html;
        }

        window.scrollToDay = function(dateStr) {
            const el = document.querySelector(`[data-date="${dateStr}"]`);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                // If specific event not found, find the section header or just nearest
                const allEvs = document.querySelectorAll('[data-date]');
                for (let el of allEvs) {
                    if (el.getAttribute('data-date') >= dateStr) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        break;
                    }
                }
            }
        }

        window.renderEvents = function() {
            const container = document.getElementById('events-list');
            if (!container) return;

            try {
                const filtered = window.allEvents.filter(e => {
                    if (!window.showPastEvents && e.event_date < TODAY_STR) return false;
                    const isMatch = window.selectedClasses.length === 0 || 
                                    e.classes.includes('All') || 
                                    e.classes.some(c => window.selectedClasses.includes(c));
                    if (!isMatch) return false;
                    if (window.hiddenCategories.includes(e.type)) return false;
                    if (window.searchQuery && !e.title.toLowerCase().includes(window.searchQuery.toLowerCase())) return false;
                    return true;
                });

                renderWeekGlance(filtered);
                renderProactiveWidgets(filtered);

                // Update Counter (Showing X of Y)
                const countDisplay = document.getElementById('event-count-display');
                if (countDisplay) {
                    const total = window.allEvents.length;
                    countDisplay.innerHTML = `Showing ${filtered.length} of ${total} total items`;
                }

                const visible = filtered.slice(0, window.visibleLimit);
                if (visible.length === 0) {
                    container.innerHTML = `
                        <div class="py-20 text-center">
                            <div class="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-gray-100">
                                <svg class="w-8 h-8 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                            </div>
                            <h3 class="text-sm font-black text-gray-900 uppercase tracking-widest mb-1 italic">No events found</h3>
                            <p class="text-[10px] text-gray-400 font-bold uppercase tracking-tighter">Try adjusting your filters or class selection</p>
                        </div>
                    `;
                    return;
                }

                container.innerHTML = visible.map(e => {
                    const isPast = e.event_date < TODAY_STR;
                    const catStyle = CATEGORY_STYLES[e.type] || CATEGORY_STYLES['OTHER'];
                    return `
                    <div id="event-${e.id}" data-date="${e.event_date}" class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm scroll-mt-24 transition-all ${isPast ? 'opacity-50 grayscale-[0.5]' : ''}">
                        <div class="flex justify-between items-center mb-3">
                            <span class="px-2 py-0.5 ${catStyle.label} text-[9px] font-black uppercase rounded border">${e.type || 'EVENT'}</span>
                            <div class="flex items-center gap-2">
                                ${isPast ? '<span class="text-[9px] font-black text-gray-400 uppercase tracking-tighter bg-gray-50 px-1.5 py-0.5 rounded">Passed</span>' : ''}
                                <span class="text-[10px] font-mono font-bold text-gray-400">${e.formatted_date_display || e.event_date}</span>
                            </div>
                        </div>
                        <h3 class="text-lg font-black text-gray-900 mb-2">${e.title}</h3>
                        <p class="text-sm text-gray-500 leading-relaxed">${e.summary || ''}</p>
                        
                        ${e.full_details && e.full_details !== e.summary ? `
                            <div id="details-${e.id}" class="hidden mt-3 pt-3 border-t border-dashed border-gray-100 animate-in fade-in slide-in-from-top-1">
                                <div class="text-sm text-gray-500 leading-relaxed whitespace-pre-wrap">${e.full_details}</div>
                            </div>
                            <button onclick="document.getElementById('details-${e.id}').classList.toggle('hidden'); this.innerText = document.getElementById('details-${e.id}').classList.contains('hidden') ? 'Show More' : 'Show Less'" 
                                    class="mt-2 text-[10px] font-black text-blue-600 uppercase tracking-widest hover:text-blue-700 transition-colors">
                                Show More
                            </button>
                        ` : ''}
    
                        <div class="mt-4 flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-gray-50">
                            <div class="flex flex-wrap gap-1.5">
                                ${e.classes.map(c => `<span class="px-2 py-0.5 bg-gray-50 border border-gray-100 text-[9px] font-bold text-gray-400 rounded">${c}</span>`).join('')}
                            </div>
                            ${e.source_title ? `
                                <div class="text-[8px] font-bold text-gray-300 uppercase tracking-tight flex items-center gap-1.5">
                                    <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"></path></svg>
                                    Source: ${e.source_title} ${e.source_date ? `(${e.source_date})` : ''}
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    `;
                }).join('');
            } catch (err) {
                console.error("Render error:", err);
                container.innerHTML = `
                    <div class="py-20 text-center">
                        <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                            <svg class="w-8 h-8 text-red-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.268 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                        </div>
                        <h3 class="text-sm font-black text-gray-900 uppercase tracking-widest mb-1 italic">Render Error</h3>
                        <p class="text-[10px] text-gray-400 font-bold uppercase tracking-tighter">Something went wrong while displaying events.</p>
                    </div>
                `;
            }
        }

        function setupEventListeners() {
            document.getElementById('search-input').addEventListener('input', (e) => {
                window.searchQuery = e.target.value;
                window.visibleLimit = 10; // Reset pagination on search
                renderEvents();
            });
            document.getElementById('show-past-toggle').addEventListener('change', (e) => {
                window.showPastEvents = e.target.checked;
                window.visibleLimit = 10; // Reset pagination
                saveState();
                renderEvents();
            });
            document.getElementById('filter-trigger').onclick = (e) => {
                e.stopPropagation();
                document.getElementById('category-panel').classList.toggle('hidden');
            };
            document.addEventListener('click', () => {
                document.getElementById('category-panel').classList.add('hidden');
                document.getElementById('sync-dropdown-menu').classList.add('hidden');
            });
            document.getElementById('category-panel').onclick = (e) => e.stopPropagation();
            document.getElementById('sync-dropdown-menu').onclick = (e) => e.stopPropagation();

            // Infinite Scroll Implementation
            const observer = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting) {
                    const totalFiltered = window.allEvents.filter(e => {
                        if (!window.showPastEvents && e.event_date < TODAY_STR) return false;
                        if (!(window.selectedClasses.length === 0 || e.classes.some(c => window.selectedClasses.includes(c)))) return false;
                        if (window.hiddenCategories.includes(e.type)) return false;
                        if (window.searchQuery && !e.title.toLowerCase().includes(window.searchQuery.toLowerCase())) return false;
                        return true;
                    }).length;

                    if (window.visibleLimit < totalFiltered) {
                        window.visibleLimit += 10;
                        renderEvents();
                    }
                }
            }, { threshold: 0.1 });

            const trigger = document.getElementById('load-more-trigger');
            if (trigger) observer.observe(trigger);
        }

        // Initialize
        debugLog('Calling boot()...');
        boot();
    