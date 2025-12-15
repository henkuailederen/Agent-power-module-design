function out = save_tempfield_2d_proj(Ttbl, outPath, varargin)
% SAVE_TEMPFIELD_2D_PROJ  Project (x,y,z,T_K) onto XY by ignoring z, 
% interpolate to a 2D grid (default 256x256), and save to HDF5.
%   out = save_tempfield_2d_proj(Ttbl, outPath, ...
%           'GridSize', [256 256], 'Interp','linear','Extrap','nearest', ...
%           'XRange',[], 'YRange',[], 'Agg','max')
%
% Inputs:
%   - Ttbl: table with variables {'x','y','z','T_K'}  (absolute temperature in Kelvin)
%   - outPath: output .h5 path (e.g., 'temp_label_256.h5')
%
% Options:
%   - GridSize: [H W], default [256 256]
%   - Interp:   'linear' | 'natural' | 'nearest'  (default 'linear')
%   - Extrap:   extrapolation method outside convex hull (default 'nearest')
%   - XRange:   [xmin xmax] (optional) default = data min/max after projection
%   - YRange:   [ymin ymax] (optional)
%   - Agg:      how to merge duplicate (x,y): 'max'(default) | 'mean' | 'median'
%
% Output:
%   - out: struct with fields {X, Y, T, valid_mask}

% ----- parse -----
ip = inputParser;
ip.addParameter('GridSize', [256 256], @(v)isnumeric(v)&&numel(v)==2);
ip.addParameter('Interp','linear', @(s)ischar(s)||isstring(s));
ip.addParameter('Extrap','nearest', @(s)ischar(s)||isstring(s));
ip.addParameter('XRange', [], @(v)isnumeric(v)&&(isempty(v)||numel(v)==2));
ip.addParameter('YRange', [], @(v)isnumeric(v)&&(isempty(v)||numel(v)==2));
ip.addParameter('Agg','max', @(s)ischar(s)||isstring(s));
ip.parse(varargin{:});
gsz   = ip.Results.GridSize;
imeth = char(ip.Results.Interp);
emeth = char(ip.Results.Extrap);
xr_opt= ip.Results.XRange;
yr_opt= ip.Results.YRange;
agg   = lower(char(ip.Results.Agg));

% ----- validate -----
reqVars = {'x','y','z','T_K'};
assert(all(ismember(reqVars, Ttbl.Properties.VariableNames)), ...
  'Ttbl must contain variables: %s', strjoin(reqVars, ', '));

x = double(Ttbl.x(:));
y = double(Ttbl.y(:));
T = double(Ttbl.T_K(:));  % absolute temperature (K)

% ----- project by ignoring z; handle exact duplicate (x,y) -----
[xy_unique, ~, ic] = unique([x y], 'rows', 'stable');
xu = xy_unique(:,1);
yu = xy_unique(:,2);

if numel(ic) ~= numel(xu)  % duplicates exist
    switch agg
        case 'mean'
            Tu = accumarray(ic, T, [], @mean);
        case 'median'
            Tu = accumarray(ic, T, [], @median);
        case 'max'
            Tu = accumarray(ic, T, [], @max);
        otherwise
            error('Unknown Agg option: %s', agg);
    end
else
    Tu = T;
end

% ----- grid -----
H = gsz(1); W = gsz(2);
if isempty(xr_opt), xr = [min(xu) max(xu)]; else, xr = xr_opt; end
if isempty(yr_opt), yr = [min(yu) max(yu)]; else, yr = yr_opt; end
xlin = linspace(xr(1), xr(2), W);
ylin = linspace(yr(1), yr(2), H);
[Xq, Yq] = meshgrid(xlin, ylin);

% ----- interpolate (valid-mask + filled) -----
F_lin = scatteredInterpolant(xu, yu, Tu, imeth, 'none');    % no extrapolation -> NaN outside
T_lin = F_lin(Xq, Yq);
valid = ~isnan(T_lin);

F_fill = scatteredInterpolant(xu, yu, Tu, imeth, emeth);    % fill outside region
Tq = F_fill(Xq, Yq);

% ----- save to HDF5 -----
Tq_single = single(Tq);
valid_uint8 = uint8(valid);

outDir = fileparts(outPath);
if ~isempty(outDir) && ~exist(outDir, 'dir'), mkdir(outDir); end
if exist(outPath, 'file')==2, delete(outPath); end

h5create(outPath, '/temp', size(Tq_single), 'Datatype', 'single');
h5write(outPath,  '/temp', Tq_single);
h5create(outPath, '/x', size(xlin), 'Datatype', 'double');
h5write(outPath,  '/x', xlin);
h5create(outPath, '/y', size(ylin), 'Datatype', 'double');
h5write(outPath,  '/y', ylin);
h5create(outPath, '/valid_mask', size(valid_uint8), 'Datatype', 'uint8');
h5write(outPath,  '/valid_mask', valid_uint8);

% attributes
h5writeatt(outPath, '/temp', 'units', 'K');                % absolute temperature
h5writeatt(outPath, '/', 'projection', 'z_ignored');       % z removed
h5writeatt(outPath, '/', 'interp', imeth);
h5writeatt(outPath, '/', 'extrap', emeth);
h5writeatt(outPath, '/', 'agg', agg);
h5writeatt(outPath, '/', 'x_range', xr);
h5writeatt(outPath, '/', 'y_range', yr);

% ----- return -----
out = struct('X', Xq, 'Y', Yq, 'T', Tq, 'valid_mask', logical(valid));
fprintf('[save_tempfield_2d_proj] Saved %dx%d temp map to %s (projection: z ignored, agg=%s)\n', ...
    H, W, outPath, agg);
end
