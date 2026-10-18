/* Presentation only: do not reinterpret labels as numbers or change calculations. */
(function (root) {
    const numbers = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 });
    const counts = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
    function number(value, integer = false) {
        if (value === null || value === undefined) return 'Sin dato';
        if (typeof value !== 'number' || !Number.isFinite(value)) return String(value);
        return (integer ? counts : numbers).format(value);
    }
    function label(value, dateGroup = false) {
        const text = value === null || value === undefined ? 'Sin dato' : String(value);
        if (!dateGroup) return text;
        const match = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}:\d{2})(\.\d+)?)?$/.exec(text);
        if (!match) return text;
        const day = `${match[3]}/${match[2]}/${match[1]}`;
        const hasTime = match[4] && (match[4] !== '00:00:00' || /[1-9]/.test(match[5] || ''));
        return hasTime ? `${day} ${match[4]}${match[5] || ''}` : day;
    }
    const api = { number, label };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.CoDataUChatPresentation = api;
})(typeof window !== 'undefined' ? window : globalThis);
